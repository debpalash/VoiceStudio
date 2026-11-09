package com.voicestudio.mobile.local

import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import java.net.*
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Foreground-only, single-model server. No file paths, model downloads or outbound requests. */
class PhoneSpeechServer(private val model: String, private val capabilities: PhoneServerCapabilities = PhoneServerCapabilities(), private val provider: suspend (String, Float) -> ByteArray) : AutoCloseable {
    val token = ByteArray(24).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
    private val busy = AtomicBoolean(false)
    private val activeRequest = AtomicReference<Pair<JsonElement, Job>?>(null)
    private val sockets = ConcurrentHashMap.newKeySet<Socket>()
    private var scope: CoroutineScope? = null
    private var listener: ServerSocket? = null
    internal var beforeClientRegistration: (() -> Unit)? = null
    private var hosts = emptySet<String>()
    var port: Int = 0; private set
    val addresses: List<String> get() = hosts.filter { it != "localhost" }.sorted().map { "http://$it:$port" }

    @Synchronized fun start(lan: Boolean = false) {
        check(listener == null)
        val socket = ServerSocket().apply { bind(InetSocketAddress(if (lan) "0.0.0.0" else "127.0.0.1", 0), 8) }
        listener = socket; port = socket.localPort
        hosts = setOf("127.0.0.1", "localhost") + if (lan) NetworkInterface.getNetworkInterfaces().toList().flatMap { it.inetAddresses.toList() }.filterIsInstance<Inet4Address>().filterNot { it.isAnyLocalAddress || it.isMulticastAddress }.map { it.hostAddress!! } else emptyList()
        val running = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope = running
        running.launch {
            try { while (isActive) {
                val client = socket.accept()
                beforeClientRegistration?.invoke()
                synchronized(this@PhoneSpeechServer) {
                    // close() shares this monitor, so an accepted socket either joins its cleanup
                    // set or is closed here. A canceled lazy job still executes completion cleanup.
                    if(listener!==socket||!running.isActive||sockets.size>=8) {client.close()}
                    else {
                        sockets.add(client)
                        val request=launch(start=CoroutineStart.LAZY) { client.use { try {
                            it.soTimeout=10_000
                            val input=readRequest(it)
                            val response=respond(input)
                            it.getOutputStream().write(response.wire())
                        } catch(e:CancellationException) {throw e} catch(_:Exception) {runCatching {it.getOutputStream().write(Reply(400).wire())}} } }
                        request.invokeOnCompletion {sockets.remove(client);runCatching {client.close()}}
                        request.start()
                    }
                }
            } } catch (_: SocketException) { /* stop closes accept */ }
        }
    }
    @Synchronized override fun close() {
        runCatching { listener?.close() }; listener = null
        scope?.cancel(); scope = null
        sockets.forEach { runCatching { it.close() } }; sockets.clear()
    }
    data class Request(val method: String, val path: String, val headers: Map<String, String>, val body: ByteArray)
    data class Reply(val status: Int, val body: ByteArray = ByteArray(0), val type: String = "application/json") {
        fun wire(): ByteArray = ("HTTP/1.1 $status Response\r\nContent-Type: $type\r\nContent-Length: ${body.size}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n").toByteArray(Charsets.US_ASCII) + body
    }
    companion object {
        fun readRequest(socket: Socket): Request {
            val input = socket.getInputStream(); val header = java.io.ByteArrayOutputStream()
            var end = false; var suffix = 0
            val deadline = System.nanoTime() + 10_000_000_000L
            fun remaining() {
                val millis = (deadline-System.nanoTime())/1_000_000L
                require(millis > 0); socket.soTimeout = millis.coerceAtMost(10_000).toInt().coerceAtLeast(1)
            }
            while (header.size() < 16_384) {
                remaining()
                val b = input.read(); require(b >= 0); header.write(b)
                suffix = (suffix shl 8) or b
                if (header.size() >= 4 && suffix == 0x0d0a0d0a) { end=true; break }
            }
            require(end)
            val lines = header.toString("US-ASCII").split("\r\n")
            val first = lines.first().split(' ')
            require(first.size == 3 && first[2] == "HTTP/1.1" && first[1].length <= 2048 && first[1].startsWith('/') && !first[1].contains('#'))
            val headers = linkedMapOf<String,String>()
            for (line in lines.drop(1).filter { it.isNotEmpty() }) {
                require(headers.size < 64 && !line.startsWith(' ') && !line.startsWith('\t'))
                val i=line.indexOf(':'); require(i>0)
                val key=line.substring(0,i).lowercase(); require(key.matches(Regex("[a-z0-9-]+")) && key !in headers)
                val value=line.substring(i+1).trim(); require(value.none { it.code < 32 || it.code > 126 })
                headers[key]=value
            }
            require("transfer-encoding" !in headers)
            val length=headers["content-length"]?.let { require(it.matches(Regex("[0-9]{1,7}"))); it.toInt() } ?: 0
            require(length <= PhoneServerAudio.MAX_REQUEST_BYTES && (first[0] != "POST" || "content-length" in headers))
            val body=ByteArray(length); var offset=0
            while(offset<length) { remaining(); val n=input.read(body,offset,length-offset);require(n>0);offset+=n }
            require(input.available()==0)
            return Request(first[0],first[1],headers,body)
        }
    }
    suspend fun respond(request: Request): Reply {
        val h=request.headers
        if(!MessageDigest.isEqual((h["authorization"] ?: "").toByteArray(),("Bearer $token").toByteArray())) return Reply(401)
        val origins=hosts.map { "http://$it:$port" }.toSet()
        if(h["host"] !in hosts.map { "$it:$port" } || (h["origin"] != null && h["origin"] !in origins)) return Reply(403)
        if(request.method=="GET"&&request.path=="/health") return jsonReply(buildJsonObject {put("status","ok");put("service","voicestudio-phone")})
        if(request.method=="GET"&&request.path=="/v1/models") return jsonReply(buildJsonObject {
            put("object","list");putJsonArray("data") {
                fun addModel(id:String,capability:String) {add(buildJsonObject {put("id",id);put("object","model");put("capability",capability)})}
                if(capabilities.speech) addModel(model,"speech")
                if(capabilities.transcribe!=null) capabilities.transcriptionModel?.let {addModel(it,"transcription")}
                if(capabilities.translate!=null) capabilities.translationModel?.let {addModel(it,"translation")}
            }
        })
        if(request.method!="POST") return Reply(405)
        if(h["content-type"]?.substringBefore(';')?.trim()?.lowercase()!="application/json") return Reply(415)
        val body=try {
            require(request.body.size<=PhoneServerAudio.MAX_REQUEST_BYTES)
            val text=Charsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(request.body)).toString()
            Json.parseToJsonElement(text).jsonObject
        } catch(_:Exception) {return Reply(400)}
        if(request.path=="/v1/audio/speech") {
            if(!capabilities.speech) return Reply(404)
            return try {
                require(body.keys.all {it in setOf("input","model","voice","response_format","speed")})
                require(body["model"]==null||string(body,"model")==model)
                require(body["voice"]==null||string(body,"voice")=="default")
                speech(string(body,"input"),speed(body),format(body["response_format"]))
            } catch(e:CancellationException) {throw e} catch(_:IllegalArgumentException) {Reply(400)} catch(_:Exception) {Reply(500)}
        }
        if(request.path=="/v1/audio/transcriptions") {
            if(capabilities.transcribe==null) return Reply(404)
            return try {
                require(body.keys.all {it in setOf("audio","model")})
                require(body["model"]==null||string(body,"model")==capabilities.transcriptionModel)
                transcribe(string(body,"audio"))
            } catch(e:CancellationException) {throw e} catch(_:IllegalArgumentException) {Reply(400)} catch(_:Exception) {Reply(500)}
        }
        if(request.path=="/v1/text/translations") {
            if(capabilities.translate==null) return Reply(404)
            return try {
                require(body.keys.all {it in setOf("input","language","model")})
                require(body["model"]==null||string(body,"model")==capabilities.translationModel)
                translate(string(body,"input"),string(body,"language"))
            } catch(e:CancellationException) {throw e} catch(_:IllegalArgumentException) {Reply(400)} catch(_:Exception) {Reply(500)}
        }
        if(request.path!="/mcp") return Reply(404)
        if(h["mcp-protocol-version"]!=null&&h["mcp-protocol-version"]!="2025-11-25") return Reply(400)
        val id=body["id"]
        fun error(code:Int):Reply=jsonReply(buildJsonObject {put("jsonrpc","2.0");put("id",id ?: JsonNull);putJsonObject("error") {put("code",code);put("message","Invalid request")}})
        if(body["jsonrpc"]!=JsonPrimitive("2.0")||(id!=null&&(id !is JsonPrimitive||id is JsonNull||(!id.isString&&id.longOrNull==null)))) return error(-32600)
        val method=runCatching {string(body,"method")}.getOrNull() ?: return error(-32600)
        if(id==null) {
            if(method=="notifications/cancelled") {
                val cancelled=runCatching {body["params"]?.jsonObject?.get("requestId")}.getOrNull()
                activeRequest.get()?.takeIf {it.first==cancelled}?.second?.cancel()
            }
            return if(method.startsWith("notifications/")) Reply(202) else error(-32600)
        }
        val result:JsonObject=try {when(method) {
            "initialize" -> {
                val params=body["params"]?.jsonObject ?: throw IllegalArgumentException();string(params,"protocolVersion")
                buildJsonObject {put("protocolVersion","2025-11-25");putJsonObject("capabilities") {putJsonObject("tools") {}};putJsonObject("serverInfo") {put("name","voicestudio-phone");put("version","1")}}
            }
            "ping" -> buildJsonObject {}
            "tools/list" -> buildJsonObject {put("tools",tools())}
            "tools/call" -> {
                val params=body["params"]?.jsonObject ?: throw IllegalArgumentException()
                val args=params["arguments"]?.jsonObject ?: throw IllegalArgumentException()
                val response=when(string(params,"name")) {
                    "speak" -> {require(capabilities.speech&&args.keys.all {it in setOf("text","speed","format")});speech(string(args,"text"),speed(args),format(args["format"]),id)}
                    "transcribe" -> {require(capabilities.transcribe!=null&&args.keys==setOf("audio"));transcribe(string(args,"audio"),id)}
                    "translate" -> {require(capabilities.translate!=null&&args.keys==setOf("text","language"));translate(string(args,"text"),string(args,"language"),id)}
                    else -> return error(-32602)
                }
                if(response.status!=200) return error(if(response.status==400) -32602 else if(response.status==429) -32000 else -32603)
                buildJsonObject {putJsonArray("content") {add(buildJsonObject {
                    if(response.type.startsWith("audio/")) {put("type","audio");put("mimeType",response.type);put("data",java.util.Base64.getEncoder().encodeToString(response.body))}
                    else {put("type","text");put("text",Json.parseToJsonElement(response.body.toString(Charsets.UTF_8)).jsonObject.getValue("text").jsonPrimitive.content)}
                })}}
            }
            else -> return error(-32601)
        }} catch(e:CancellationException) {throw e} catch(_:IllegalArgumentException) {return error(-32602)} catch(_:Exception) {return error(-32603)}
        return jsonReply(buildJsonObject {put("jsonrpc","2.0");put("id",id);put("result",result)})
    }
    private fun tools():JsonArray=buildJsonArray {
        fun schema(description:String)=buildJsonObject {put("type","string");put("description",description);put("minLength",1);put("maxLength",2000)}
        fun tool(name:String,description:String,properties:JsonObject,required:List<String>) {add(buildJsonObject {put("name",name);put("description",description);putJsonObject("inputSchema") {put("type","object");put("properties",properties);putJsonArray("required") {required.forEach {add(it)}};put("additionalProperties",false)}})}
        if(capabilities.speech) tool("speak","Generate speech with the selected installed phone voice.",buildJsonObject {
            put("text",schema("Text to speak"));putJsonObject("speed") {put("type","number");put("minimum",0.5);put("maximum",2)}
            putJsonObject("format") {put("type","string");putJsonArray("enum") {add("wav");if(capabilities.encodeMP3!=null) add("mp3")}}
        },listOf("text"))
        if(capabilities.transcribe!=null) tool("transcribe","Transcribe base64 PCM16 mono WAV (8–24 kHz, up to 30 seconds) using the enabled installed model.",buildJsonObject {putJsonObject("audio") {put("type","string");put("maxLength",2_000_000)}},listOf("audio"))
        if(capabilities.translate!=null) tool("translate","Translate text using the enabled installed phone model.",buildJsonObject {put("text",schema("Text to translate"));putJsonObject("language") {put("type","string");putJsonArray("enum") {capabilities.translationLanguages.forEach {add(it)}}}},listOf("text","language"))
    }
    private fun jsonReply(value:JsonObject)=Reply(200,value.toString().toByteArray())
    private fun string(value:JsonObject,key:String):String {val p=value[key] as? JsonPrimitive ?: throw IllegalArgumentException();require(p.isString);return p.content}
    private fun speed(value:JsonObject):Float {
        val number=value["speed"] ?: return 1f
        require(number is JsonPrimitive&&!number.isString)
        return number.float.also {require(it.isFinite()&&it in 0.5f..2f)}
    }
    private fun format(value:JsonElement?):String {
        if(value==null) return "wav"
        require(value is JsonPrimitive&&value.isString)
        return value.content.also {require(it=="wav"||(it=="mp3"&&capabilities.encodeMP3!=null))}
    }
    private fun valid(text:String) {require(text.isNotBlank()&&text.length<=2000)}
    private suspend fun speech(text:String,speed:Float,format:String,id:JsonElement?=null):Reply {
        valid(text)
        return process(if(format=="mp3") "audio/mpeg" else "audio/wav",id) {
            val wave=provider(text,speed)
            if(format=="mp3") requireNotNull(capabilities.encodeMP3).invoke(wave) else wave
        }
    }
    private suspend fun transcribe(base64:String,id:JsonElement?=null):Reply {
        require(base64.length<=2_000_000&&base64.length%4==0)
        val audio=PhoneServerAudio.decodeWave(java.util.Base64.getDecoder().decode(base64))
        val transcriber=requireNotNull(capabilities.transcribe)
        return process(id=id) {buildJsonObject {put("text",transcriber(audio))}.toString().toByteArray()}
    }
    private suspend fun translate(text:String,language:String,id:JsonElement?=null):Reply {
        valid(text)
        val selected=requireNotNull(capabilities.translationLanguages.firstOrNull {it.equals(language,true)})
        val translator=requireNotNull(capabilities.translate)
        return process(id=id) {buildJsonObject {put("text",translator(text,selected))}.toString().toByteArray()}
    }
    private suspend fun process(type:String="application/json",id:JsonElement?=null,operation:suspend ()->ByteArray):Reply {
        if(!busy.compareAndSet(false,true)) return Reply(429)
        try {
            val bytes=coroutineScope {
                val work=async {operation()}
                if(id!=null) activeRequest.set(id to work)
                try {work.await()} finally {if(id!=null) activeRequest.set(null)}
            }
            return Reply(200,bytes,type)
        } finally {busy.set(false)}
    }
}
