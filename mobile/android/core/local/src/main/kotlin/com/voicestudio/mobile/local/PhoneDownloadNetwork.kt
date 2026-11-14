package com.voicestudio.mobile.local

import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities

/** Observe the app's default route without probing, requesting or binding another network. */
class PhoneDownloadNetwork(private val connectivity: ConnectivityManager) : AutoCloseable {
    private val lock = Any()
    private var network = connectivity.activeNetwork
    private var capabilities = network?.let(connectivity::getNetworkCapabilities)
    private var blocked = false
    private var closed = false
    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(value: Network) = synchronized(lock) {
            if (!closed && network != value) { network = value; capabilities = null; blocked = false }
        }
        override fun onCapabilitiesChanged(value: Network, updated: NetworkCapabilities) = synchronized(lock) {
            if (!closed && network == value) capabilities = NetworkCapabilities(updated)
        }
        override fun onBlockedStatusChanged(value: Network, isBlocked: Boolean) = synchronized(lock) {
            if (!closed && network == value) blocked = isBlocked
        }
        override fun onLost(value: Network) = synchronized(lock) {
            if (network == value) { network = null; capabilities = null }
        }
    }
    init { connectivity.registerDefaultNetworkCallback(callback) }

    @Suppress("DEPRECATION")
    val wifiAllowed: Boolean get() = synchronized(lock) {
        val current = capabilities
        if (closed || blocked || network == null || network != connectivity.activeNetwork || current == null ||
            !current.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) ||
            !current.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) || connectivity.isActiveNetworkMetered ||
            current.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) return@synchronized false
        val physical = current.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || current.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
        if (!current.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) return@synchronized physical
        // This public API describes the default VPN's own underlying route.
        // Never infer permission from an unrelated Wi-Fi network in allNetworks.
        val underlying = connectivity.activeNetworkInfo
        if (underlying?.isConnected == true && underlying.type == ConnectivityManager.TYPE_MOBILE) return@synchronized false
        physical || underlying?.let { it.isConnected && it.type in setOf(ConnectivityManager.TYPE_WIFI, ConnectivityManager.TYPE_ETHERNET) } == true
    }

    override fun close() {
        val unregister = synchronized(lock) {
            if (closed) false else { closed = true; capabilities = null; true }
        }
        if (unregister) connectivity.unregisterNetworkCallback(callback)
    }
}
