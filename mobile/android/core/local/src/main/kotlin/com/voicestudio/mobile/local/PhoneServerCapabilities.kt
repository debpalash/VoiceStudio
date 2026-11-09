package com.voicestudio.mobile.local

/** Only explicitly enabled, installed models are exposed; no file paths or model downloads. */
data class PhoneServerCapabilities(
    val speech:Boolean=true,
    val transcriptionModel:String?=null,
    val translationModel:String?=null,
    val transcribe:(suspend (LocalAudio)->String)?=null,
    val translate:(suspend (String,String)->String)?=null,
    val translationLanguages:List<String> = emptyList(),
    val encodeMP3:(suspend (ByteArray)->ByteArray)?=null
)
object PhoneServerAudio {
    const val MAX_REQUEST_BYTES=2_097_152
    fun decodeWave(bytes:ByteArray):LocalAudio {
        require(bytes.size in 44..1_500_000)
        fun text(start:Int,end:Int)=bytes.copyOfRange(start,end).toString(Charsets.US_ASCII)
        fun u16(at:Int)=(bytes[at].toInt() and 255) or ((bytes[at+1].toInt() and 255) shl 8)
        fun u32(at:Int):Long=(0..3).fold(0L) {result,index -> result or ((bytes[at+index].toLong() and 255) shl (index*8))}
        require(text(0,4)=="RIFF"&&text(8,12)=="WAVE"&&u32(4)==bytes.size.toLong()-8)
        var position=12;var rate:Int?=null;var payload:IntRange?=null
        while(position+8<=bytes.size) {
            val length=u32(position+4);val start=position+8;require(length<=bytes.size.toLong()-start);val size=length.toInt()
            when(text(position,position+4)) {
                "fmt " -> {
                    require(rate==null&&size in 16..64&&u16(start)==1&&u16(start+2)==1&&u16(start+14)==16&&u16(start+12)==2)
                    val sampleRate=u32(start+4);require(sampleRate in 8000L..24000L&&u32(start+8)==sampleRate*2)
                    rate=sampleRate.toInt()
                }
                "data" -> {require(payload==null&&size>0&&size%2==0);payload=start until start+size}
            }
            position=start+size+size%2
        }
        val sampleRate=requireNotNull(rate);val samples=requireNotNull(payload)
        require(position==bytes.size&&(samples.last-samples.first+1)/2<=sampleRate*30)
        val pcm=FloatArray((samples.last-samples.first+1)/2) {index -> u16(samples.first+index*2).toShort()/32768f}
        return LocalAudio(pcm,sampleRate)
    }
    fun speech(audio:LocalAudio,speed:Float):LocalAudio {
        require(audio.sampleRate==24000)
        return LocalAudio(NarrationAudio.speed(audio.samples,speed),24000)
    }
}
