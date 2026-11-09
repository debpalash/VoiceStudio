package com.voicestudio.mobile.local

import kotlin.math.*

internal object NarrationText {
    fun rules(values: Map<String,String>): List<Pair<Regex,String>> = values.entries.sortedWith(compareByDescending<Map.Entry<String,String>> {it.key.length}.thenBy {it.key}).map {(word,spoken)->
        require(word.isNotBlank()&&word.length<=100&&spoken.isNotBlank()&&spoken.length<=500)
        Regex("(?<![\\p{L}\\p{N}_])"+Regex.escape(word)+"(?![\\p{L}\\p{N}_])") to spoken
    }
    fun apply(line: String,rules: List<Pair<Regex,String>>): String {
        val matches=mutableListOf<Pair<IntRange,String>>()
        for((regex,spoken) in rules) for(match in regex.findAll(line)) if(matches.none {it.first.first<=match.range.last&&match.range.first<=it.first.last}) matches+=match.range to spoken
        val result=StringBuilder();var cursor=0
        for((range,spoken) in matches.sortedBy {it.first.first}) {
            require(result.length+range.first-cursor+spoken.length<=1_000_000)
            result.append(line,cursor,range.first).append(spoken);cursor=range.last+1
        }
        require(result.length+line.length-cursor<=1_000_000)
        return result.append(line,cursor,line.length).toString()
    }
}

/** Bounded pitch-preserving overlap-add, identical parameters on both phones. */
internal object NarrationAudio {
    fun speed(input: FloatArray,rate: Float): FloatArray {
        require(rate.isFinite()&&rate in 0.5f..2f&&input.isNotEmpty()&&input.size<=14_400_000)
        if(abs(rate-1)<0.0001f) return input
        val count=max(1,(input.size.toDouble()/rate).roundToInt());val window=min(960,input.size);val hop=max(1,min(240,window/4))
        val output=FloatArray(count+window);val weights=FloatArray(count+window)
        val envelope=FloatArray(window) {(0.5-0.5*cos(2*PI*(it+0.5)/window)).toFloat()}
        var destination=0
        while(destination<count) {
            val expected=min(max(0,(destination.toDouble()*rate).toInt()),input.size-window);var source=expected
            if(destination>0&&window>=240) {
                var best=Double.NEGATIVE_INFINITY
                for(candidate in max(0,expected-120)..min(input.size-window,expected+120) step 8) {
                    var correlation=0.0;var energy=0.000001
                    for(offset in 0 until min(window-hop,count-destination) step 8) {
                        val prior=if(weights[destination+offset]>0) output[destination+offset]/weights[destination+offset] else 0f
                        val sample=input[candidate+offset];correlation+=prior*sample;energy+=sample*sample
                    }
                    val score=correlation/sqrt(energy);if(score>best) {best=score;source=candidate}
                }
            }
            for(offset in 0 until min(window,count-destination)) {output[destination+offset]+=input[source+offset]*envelope[offset];weights[destination+offset]+=envelope[offset]}
            destination+=hop
        }
        return FloatArray(count) {if(weights[it]>0) output[it]/weights[it] else 0f}
    }
}
