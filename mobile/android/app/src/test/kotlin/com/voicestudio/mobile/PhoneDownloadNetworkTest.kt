package com.voicestudio.mobile

import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkInfo
import com.voicestudio.mobile.local.PhoneDownloadNetwork
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.ArgumentCaptor
import org.mockito.ArgumentMatchers.any
import org.mockito.Mockito.*
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowNetwork

@Suppress("DEPRECATION")
@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class PhoneDownloadNetworkTest {
    private fun capabilities(transport: Int, unmetered: Boolean=true): NetworkCapabilities {
        val value=NetworkCapabilities()
        shadowOf(value).apply {
            addTransportType(transport);addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            if(unmetered)addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
            else removeCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
            if(transport==NetworkCapabilities.TRANSPORT_VPN)removeCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
        }
        return value
    }
    private class Route(val caps: NetworkCapabilities) {
        val manager=mock(ConnectivityManager::class.java)
        var current: Network?=ShadowNetwork.newInstance(42)
        var metered=false
        var underlying: NetworkInfo?=null
        val observer: PhoneDownloadNetwork
        val callback: ConnectivityManager.NetworkCallback
        init {
            `when`(manager.activeNetwork).thenAnswer {current}
            `when`(manager.getNetworkCapabilities(any(Network::class.java))).thenReturn(caps)
            `when`(manager.isActiveNetworkMetered).thenAnswer {metered}
            `when`(manager.activeNetworkInfo).thenAnswer {underlying}
            observer=PhoneDownloadNetwork(manager)
            val capture=ArgumentCaptor.forClass(ConnectivityManager.NetworkCallback::class.java)
            verify(manager).registerDefaultNetworkCallback(capture.capture())
            callback=capture.value
        }
        fun physical(type: Int) {
            underlying=mock(NetworkInfo::class.java).also {
                `when`(it.isConnected).thenReturn(true);`when`(it.type).thenReturn(type)
            }
        }
    }
    @Test fun vpnUsesOnlyItsOwnUnmeteredWifiOrEthernetProof() {
        val route=Route(capabilities(NetworkCapabilities.TRANSPORT_VPN))
        try {
            assertFalse(route.observer.wifiAllowed)
            route.physical(ConnectivityManager.TYPE_WIFI);assertTrue(route.observer.wifiAllowed)
            route.metered=true;assertFalse(route.observer.wifiAllowed)
            route.metered=false;route.physical(ConnectivityManager.TYPE_MOBILE)
            assertFalse("Unmetered cellular is not Wi-Fi permission",route.observer.wifiAllowed)
            route.physical(ConnectivityManager.TYPE_ETHERNET);assertTrue(route.observer.wifiAllowed)
            verify(route.manager,never()).bindProcessToNetwork(any())
        }finally{route.observer.close()}
    }
    @Test fun directNetworksKeepWifiMeteringAndCellularBoundaries() {
        val route=Route(capabilities(NetworkCapabilities.TRANSPORT_WIFI))
        try {
            assertTrue(route.observer.wifiAllowed)
            route.callback.onAvailable(route.current!!);assertTrue(route.observer.wifiAllowed)
            route.metered=true;assertFalse(route.observer.wifiAllowed);route.metered=false
            route.callback.onCapabilitiesChanged(route.current!!,capabilities(NetworkCapabilities.TRANSPORT_WIFI,false))
            assertFalse(route.observer.wifiAllowed)
            route.callback.onCapabilitiesChanged(route.current!!,capabilities(NetworkCapabilities.TRANSPORT_CELLULAR))
            assertFalse(route.observer.wifiAllowed)
            route.callback.onCapabilitiesChanged(route.current!!,capabilities(NetworkCapabilities.TRANSPORT_ETHERNET))
            assertTrue(route.observer.wifiAllowed)
        }finally{route.observer.close()}
    }
    @Test fun defaultRouteChangesRecoverWithoutPollingAndIgnoreStaleLossCallbacks() {
        val route=Route(capabilities(NetworkCapabilities.TRANSPORT_WIFI))
        val old=route.current!!
        try {
            val vpn=ShadowNetwork.newInstance(43);route.current=vpn
            assertFalse("An unobserved new route must not inherit old permission",route.observer.wifiAllowed)
            route.callback.onAvailable(vpn);assertFalse(route.observer.wifiAllowed)
            route.physical(ConnectivityManager.TYPE_WIFI)
            route.callback.onCapabilitiesChanged(vpn,capabilities(NetworkCapabilities.TRANSPORT_VPN))
            assertTrue(route.observer.wifiAllowed)
            route.callback.onLost(old);assertTrue(route.observer.wifiAllowed)
            route.callback.onBlockedStatusChanged(vpn,true);assertFalse(route.observer.wifiAllowed)
            route.callback.onBlockedStatusChanged(vpn,false);assertTrue(route.observer.wifiAllowed)
            route.physical(ConnectivityManager.TYPE_MOBILE);assertFalse(route.observer.wifiAllowed)
            route.physical(ConnectivityManager.TYPE_WIFI);assertTrue(route.observer.wifiAllowed)
            route.callback.onLost(vpn);assertFalse(route.observer.wifiAllowed)
        }finally{route.observer.close()}
        route.observer.close()
        verify(route.manager,times(1)).unregisterNetworkCallback(route.callback)
        route.callback.onAvailable(old);route.callback.onCapabilitiesChanged(old,route.caps)
        assertFalse(route.observer.wifiAllowed)
    }
}
