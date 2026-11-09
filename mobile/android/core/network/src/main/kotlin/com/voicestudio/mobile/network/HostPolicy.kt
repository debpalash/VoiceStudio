package com.voicestudio.mobile.network

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.net.InetAddress
import java.net.Inet6Address

class ClientFailure(val key: String, val params: Map<String, String> = emptyMap(), val status: Int? = null) : java.io.IOException(key)

data class PairingAddress(val baseUrl: HttpUrl, val pin: String?) {
    companion object {
        fun parse(input: String, explicitPin: String? = null): PairingAddress {
            val url = input.trim().toHttpUrlOrNull() ?: throw ClientFailure("mobile.connect.error_invalid_url")
            if (url.username.isNotEmpty() || url.password.isNotEmpty() || url.fragment != null ||
                url.queryParameterNames.any { it != "pin" } || url.queryParameterValues("pin").size > 1) {
                throw ClientFailure("mobile.connect.error_invalid_url")
            }
            val pin = explicitPin?.takeIf { it.isNotBlank() } ?: url.queryParameter("pin")
            if (pin != null && !Regex("[0-9]{6}").matches(pin)) throw ClientFailure("mobile.connect.error_pin")
            HostPolicy.validate(url)
            return PairingAddress(url.newBuilder().query(null).encodedPath(url.encodedPath.trimEnd('/') + "/").build(), pin)
        }
    }
}

object HostPolicy {
    fun isPrivateAddress(address: InetAddress): Boolean {
        val b = address.address.map { it.toInt() and 255 }
        if (b.size == 4) return b[0] == 127 || b[0] == 10 ||
            (b[0] == 172 && b[1] in 16..31) || (b[0] == 192 && b[1] == 168) ||
            (b[0] == 100 && b[1] in 64..127) || (b[0] == 169 && b[1] == 254)
        return address.isLoopbackAddress || (address is Inet6Address &&
            ((b[0] and 254) == 252 || (b[0] == 254 && (b[1] and 192) == 128)))
    }
    fun cleartextAllowed(host: String): Boolean {
        val h = host.removeSurrounding("[", "]").lowercase().trimEnd('.')
        if (h == "localhost" || h.endsWith(".localhost") || h.endsWith(".local") || h.endsWith(".ts.net")) return true
        if (!h.contains(':') && !Regex("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+").matches(h)) return false
        return runCatching { isPrivateAddress(InetAddress.getByName(h)) }.getOrDefault(false)
    }
    fun validate(url: HttpUrl) {
        if (url.scheme != "https" && (url.scheme != "http" || !cleartextAllowed(url.host)))
            throw ClientFailure("mobile.connect.error_insecure")
    }
    fun sameOrigin(a: HttpUrl, b: HttpUrl) = a.scheme == b.scheme && a.host == b.host && a.port == b.port
}
