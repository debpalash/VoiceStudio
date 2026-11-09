package com.voicestudio.mobile.local

/** These are operator-assigned labels, never fabricated acoustic identities. */
object PhoneDubCast {
    fun valid(segment: DubSegmentState): Boolean = segment.speaker.orEmpty().length<=80&&segment.speaker.orEmpty().none(Char::isISOControl)&&segment.referenceOrigin in listOf(null,"cast","segment")&&segment.voiceID.orEmpty().length<=100
    fun isLineOverride(segment: DubSegmentState)=segment.referenceOrigin=="segment"||(segment.referenceOrigin==null&&segment.referenceFile!=null)
    fun speakers(segments: List<DubSegmentState>)=segments.mapNotNull {it.speaker?.trim()?.takeIf(String::isNotEmpty)}.distinct().sorted()
    fun assigning(segments: List<DubSegmentState>,speaker: String,voiceID: String,replaceOverrides: Boolean=false)=segments.map {segment ->
        if(segment.speaker?.trim()==speaker&&(replaceOverrides||!isLineOverride(segment))) segment.copy(speaker=speaker,referenceOrigin="cast",voiceID=voiceID) else segment
    }
}
