package com.voicestudio.mobile.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import androidx.datastore.core.DataStore
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.encodeToString
import java.security.KeyStore
import java.io.IOException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import com.voicestudio.mobile.network.*

private val Context.dataStore by preferencesDataStore("voicestudio")

/** Persisted state must survive app updates that add, drop or rename fields:
 * the strict default Json failed on any unknown key and reset pairing and
 * consent to defaults on upgrade. */
internal val StoreJson = Json { ignoreUnknownKeys = true; coerceInputValues = true }
@Serializable data class SavedHost(val url: String, val label: String, val lastSeen: Long)
@Serializable data class Secret(val token: String? = null, val pin: String? = null)
@Serializable data class Preferences(val consent: Boolean? = null, val activeUrl: String? = null, val hosts: List<SavedHost> = emptyList(),
    val mode: String = "dark", val lightPalette: String = "default", val darkPalette: String = "default", val locale: String = "system", val notifications: Boolean = false, val runLocations: Map<String,String> = emptyMap(), val phoneModels: Map<String,String> = emptyMap(), val allowMobileModels: Boolean = false)
@Serializable data class DubCheckpoint(val uploaded: Boolean = false, val prepTask: String = "", val segments: List<Segment> = emptyList(),
    val source: String? = null, val nextLanguage: Int = 0, val renderTask: String = "")

/** AES/GCM secret storage: only the encryption key lives in Android Keystore.
 * Master API keys are accepted as transient arguments and never serialized. */
interface CredentialVault { fun write(host: String, secret: Secret?); fun read(host: String): Secret? }
class SecretVault(context: Context) : CredentialVault {
    private val prefs = context.getSharedPreferences("sessions", Context.MODE_PRIVATE)
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("voicestudio.sessions", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("voicestudio.sessions", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    @Synchronized override fun write(host: String, secret: Secret?) {
        if(secret == null) { check(prefs.edit().remove(host).commit()); return }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()); updateAAD(host.toByteArray()) }
        val ciphertext = cipher.iv + cipher.doFinal(StoreJson.encodeToString(secret).toByteArray())
        check(prefs.edit().putString(host, Base64.encodeToString(ciphertext, Base64.NO_WRAP)).commit())
    }
    @Synchronized override fun read(host: String): Secret? = prefs.getString(host, null)?.let { encoded ->
        runCatching {
            val bytes = Base64.decode(encoded, Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12))); updateAAD(host.toByteArray())
            }
            StoreJson.decodeFromString<Secret>(cipher.doFinal(bytes.copyOfRange(12, bytes.size)).decodeToString())
        }.getOrNull()
    }
}

class SessionStore(context: Context, val vault: CredentialVault = SecretVault(context.applicationContext),
    private val store: DataStore<androidx.datastore.preferences.core.Preferences> = context.applicationContext.dataStore) {
    private val stateKey = stringPreferencesKey("preferences")
    private val readableData = store.data.catch { error ->
        if(error is IOException) emit(emptyPreferences()) else throw error
    }
    private fun decodePreferences(data: androidx.datastore.preferences.core.Preferences): Preferences =
        data[stateKey]?.let { runCatching { StoreJson.decodeFromString<Preferences>(it) }.getOrNull() } ?: Preferences()
    val preferences: Flow<Preferences> = readableData.map(::decodePreferences)
    suspend fun update(transform: (Preferences) -> Preferences) {
        store.edit { data ->
            val previous = decodePreferences(data)
            val next = transform(previous)
            if(previous.activeUrl != next.activeUrl) {
                data.asMap().keys.filter { it.name.startsWith("dub.") }.forEach { data.remove(it) }
            }
            data[stateKey] = StoreJson.encodeToString(next)
        }
    }
    suspend fun pair(address: PairingAddress, secret: Secret) {
        val url = address.baseUrl.toString()
        withContext(Dispatchers.IO) {vault.write(url, secret)}
        update { previous ->
            val saved = previous.hosts.firstOrNull { it.url == url }
            val host = saved?.copy(lastSeen = System.currentTimeMillis())
                ?: SavedHost(url, address.baseUrl.host, System.currentTimeMillis())
            previous.copy(activeUrl = url, hosts = listOf(host) + previous.hosts.filterNot { it.url == url })
        }
    }
    suspend fun disconnect() {
        val current = preferences.first()
        withContext(Dispatchers.IO) {current.activeUrl?.let { vault.write(it, null) }}
        update { it.copy(activeUrl = null) }
    }
    suspend fun forget(url: String) { withContext(Dispatchers.IO) {vault.write(url, null)}; update { it.copy(hosts = it.hosts.filterNot { h -> h.url == url }, activeUrl = it.activeUrl?.takeUnless { a -> a == url }) } }
    suspend fun label(url: String, label: String) { update { it.copy(hosts=it.hosts.map { h -> if(h.url==url) h.copy(label=label) else h }) } }
    suspend fun api(url: String, expired: () -> Unit = {}): VoiceStudioApi = withContext(Dispatchers.IO) {
        val secret = vault.read(url)
        VoiceStudioApi(PairingAddress.parse(url, secret?.pin), secret?.token, expired)
    }
    /** Receipt IDs survive host switches; acknowledged failures never reappear. */
    suspend fun consumeDubFailure(id: java.util.UUID): Boolean {
        val key = stringSetPreferencesKey("seenDubFailures")
        var unseen = false
        store.edit { data ->
            val seen = data[key].orEmpty()
            unseen = id.toString() !in seen
            if(unseen) data[key] = seen + id.toString()
        }
        return unseen
    }
    suspend fun checkpoint(host: String, job: String): DubCheckpoint = readableData.first()[stringPreferencesKey("dub.$host.$job")]?.let {
        runCatching { StoreJson.decodeFromString<DubCheckpoint>(it) }.getOrNull()
    } ?: DubCheckpoint()
    suspend fun checkpoint(host: String, job: String, value: DubCheckpoint) {
        store.edit { data ->
            val active = decodePreferences(data).activeUrl
            if(active == host) data[stringPreferencesKey("dub.$host.$job")] = StoreJson.encodeToString(value)
        }
    }
}
