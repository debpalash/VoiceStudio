package com.voicestudio.mobile.features

import android.util.AtomicFile
import com.voicestudio.mobile.local.ProductionProject
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import java.util.UUID

/** Private editor checkpoints, separate from named projects and rendered audio. */
class ProductionDraftRepository(private val root: File) {
    private val json=Json {ignoreUnknownKeys=true;encodeDefaults=true}
    // AtomicFile itself does not synchronize separate instances. A recreated
    // editor must not open/delete the replacement an old IO writer is finishing.
    private companion object {val ioLock=Any()}

    private fun checkpoint(id: String): AtomicFile {
        require(UUID.fromString(id).toString()==id)
        return AtomicFile(File(root,"$id.json"))
    }

    fun save(project: ProductionProject): String = synchronized(ioLock) {
        saveCheckpoint(project)
    }

    /** Recheck editor ownership after waiting for an earlier disk operation. */
    fun saveIfCurrent(project: ProductionProject,isCurrent: ()->Boolean): String? = synchronized(ioLock) {
        if(isCurrent()) saveCheckpoint(project) else null
    }

    private fun saveCheckpoint(project: ProductionProject): String {
        val file=checkpoint(project.id)
        val bytes=json.encodeToString(ProductionProject.serializer(),project).toByteArray(Charsets.UTF_8)
        if(!root.isDirectory&&!root.mkdirs()&&!root.isDirectory) throw IOException("Draft directory is unavailable")
        val stream=file.startWrite()
        try {
            stream.write(bytes)
            stream.fd.sync()
            file.finishWrite(stream)
        } catch(error:Exception) {
            file.failWrite(stream)
            throw error
        }
        // AtomicFile reports a failed final rename through Android's log. Do
        // not publish a handle until the complete replacement is readable.
        if(!file.openRead().use {it.readBytes().contentEquals(bytes)}) throw IOException("Draft checkpoint was not published")
        return project.id
    }

    fun restore(saved: String): ProductionProject = synchronized(ioLock) {
        // Existing saved instances used the complete JSON value. Keep those
        // edits intact; their next successful save migrates to the UUID form.
        val project=if(saved.trimStart().startsWith("{")) {
            json.decodeFromString(ProductionProject.serializer(),saved)
        } else {
            val value=checkpoint(saved).openRead().use {it.readBytes().toString(Charsets.UTF_8)}
            json.decodeFromString(ProductionProject.serializer(),value).also {require(it.id==saved)}
        }
        require(UUID.fromString(project.id).toString()==project.id)
        project
    }

    fun delete(id: String) = synchronized(ioLock) {
        val file=checkpoint(id)
        file.delete()
        val base=file.baseFile
        if(listOf(base,File(base.path+".new"),File(base.path+".bak")).any {it.exists()}) {
            throw IOException("Draft checkpoint could not be removed")
        }
    }
}
