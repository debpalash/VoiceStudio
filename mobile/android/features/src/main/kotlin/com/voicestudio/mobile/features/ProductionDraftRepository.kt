package com.voicestudio.mobile.features

import android.util.AtomicFile
import com.voicestudio.mobile.local.ProductionProject
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import java.io.FileNotFoundException
import java.nio.file.Files
import java.nio.file.NoSuchFileException
import java.nio.file.LinkOption
import java.nio.file.attribute.BasicFileAttributes
import java.util.UUID

/** Private editor checkpoints, separate from named projects and rendered audio. */
class ProductionDraftRepository(private val root: File) {
    private val json=Json {ignoreUnknownKeys=true;encodeDefaults=true}
    // AtomicFile itself does not synchronize separate instances. A recreated
    // editor must not open/delete the replacement an old IO writer is finishing.
    private companion object {
        val ioLock=Any()
        val ownerLock=Any()
        var generation=0L
        val owners=mutableMapOf<String,OwnerRecord>()
    }
    data class EditorSnapshot(val project: ProductionProject,val durable: String?,val activityReference: String?,val retired: Set<String>)
    class Owner internal constructor(internal val key: String,internal val generation: Long,internal val kind: String,var previous: EditorSnapshot?)
    private class OwnerRecord(val generation: Long,var snapshot: ()->EditorSnapshot?,var references: ()->Set<String>)

    fun acquireOwner(kind: String,references: (() ->Set<String>)?=null,snapshot: ()->EditorSnapshot?): Owner = synchronized(ownerLock) {
        require(kind in listOf("stories","audiobook"))
        val key=root.absoluteFile.toPath().normalize().toString()+":"+kind
        val previous=owners[key]?.snapshot?.invoke()
        val next=++generation
        owners[key]=OwnerRecord(next,snapshot,references ?: {snapshot()?.let {setOfNotNull(it.project.id,it.durable,it.activityReference)} ?: emptySet()})
        Owner(key,next,kind,previous)
    }
    fun owns(owner: Owner)=synchronized(ownerLock) {owners[owner.key]?.generation==owner.generation}
    fun handoff(owner: Owner,snapshot: EditorSnapshot?)=synchronized(ownerLock) {
        owners[owner.key]?.takeIf {it.generation==owner.generation}?.let {
            it.snapshot={snapshot}
            it.references={snapshot?.let {saved ->setOfNotNull(saved.project.id,saved.durable,saved.activityReference)} ?: emptySet()}
        }
    }
    fun release(owner: Owner)=synchronized(ownerLock) {
        if(owners[owner.key]?.generation==owner.generation) owners.remove(owner.key)
        Unit
    }

    private fun checkpoint(id: String): AtomicFile {
        require(UUID.fromString(id).toString()==id)
        return AtomicFile(File(root,"$id.json"))
    }

    fun save(project: ProductionProject): String = synchronized(ioLock) {
        checkNotNull(saveCheckpoint(project))
    }

    /** Recheck editor ownership after waiting for an earlier disk operation. */
    fun saveIfCurrent(project: ProductionProject,isCurrent: ()->Boolean): String? = synchronized(ioLock) {
        if(isCurrent()) saveCheckpoint(project,isCurrent) else null
    }

    fun saveOwned(project: ProductionProject,owner: Owner,isCurrent: ()->Boolean): String? = synchronized(ioLock) {
        require(project.kind==owner.kind)
        val valid={owns(owner)&&isCurrent()}
        val handle=if(valid()) saveCheckpoint(project,valid) else null
        if(handle!=null&&writeActive(owner.kind,handle,valid)) handle else null
    }

    /** A pointer is published only for a complete, matching checkpoint. */
    fun activateOwned(project: ProductionProject,owner: Owner,isCurrent: ()->Boolean): Boolean = synchronized(ioLock) {
        require(project.kind==owner.kind)
        val valid={owns(owner)&&isCurrent()}
        if(!valid()) return@synchronized false
        if(restore(project.id)!=project) return@synchronized false
        writeActive(owner.kind,project.id,valid)
    }

    private fun activeFile(kind: String): AtomicFile {
        require(kind in listOf("stories","audiobook"))
        return AtomicFile(File(root,".active-$kind"))
    }

    private fun activeReference(kind: String): String? {
        val bytes=try {
            activeFile(kind).openRead().use {input ->
                val buffer=ByteArray(65);var size=0
                while(size<buffer.size) {val n=input.read(buffer,size,buffer.size-size);if(n<0) break;size+=n}
                if(size==buffer.size) throw IOException("Draft reference is oversized")
                buffer.copyOf(size)
            }
        } catch(error:FileNotFoundException) {
            // FileInputStream also uses FileNotFoundException for EACCES.
            // Only proven absence of both AtomicFile names means no pointer.
            val base=activeFile(kind).baseFile
            for(path in listOf(base,File(base.path+".bak"))) {
                try {Files.readAttributes(path.toPath(),BasicFileAttributes::class.java,LinkOption.NOFOLLOW_LINKS);throw error}
                catch(_:NoSuchFileException) { /* inspect the backup too */ }
            }
            return null
        }
        val parts=bytes.toString(Charsets.UTF_8).split('\n')
        if(parts.size!=3||parts[0]!="1"||parts[2].isNotEmpty()) throw IOException("Draft reference is invalid")
        val id=parts[1]
        if(runCatching {UUID.fromString(id).toString()==id}.getOrDefault(false).not()) throw IOException("Draft reference is invalid")
        return id
    }

    private fun writeActive(kind: String,id: String,valid: ()->Boolean): Boolean {
        checkpoint(id) // validate the same canonical UUID used by checkpoint paths
        if(!valid()) return false
        val file=activeFile(kind)
        val bytes="1\n$id\n".toByteArray(Charsets.UTF_8)
        val stream=file.startWrite()
        try {
            stream.write(bytes);stream.fd.sync()
            if(!valid()) {file.failWrite(stream);return false}
            file.finishWrite(stream)
        } catch(error:Exception) {file.failWrite(stream);throw error}
        if(activeReference(kind)!=id) throw IOException("Draft reference was not published")
        return valid()
    }

    /** Used only when Activity state supplies no reference; never overrides one. */
    fun restoreActive(kind: String): ProductionProject? = synchronized(ioLock) {
        val id=activeReference(kind) ?: return@synchronized null
        restore(id).also {require(it.kind==kind)}
    }

    private fun saveCheckpoint(project: ProductionProject,valid: ()->Boolean = {true}): String? {
        val file=checkpoint(project.id)
        val bytes=json.encodeToString(ProductionProject.serializer(),project).toByteArray(Charsets.UTF_8)
        if(!root.isDirectory&&!root.mkdirs()&&!root.isDirectory) throw IOException("Draft directory is unavailable")
        val stream=file.startWrite()
        try {
            stream.write(bytes)
            stream.fd.sync()
            if(!valid()) {file.failWrite(stream);return null}
            file.finishWrite(stream)
        } catch(error:Exception) {
            file.failWrite(stream)
            throw error
        }
        // AtomicFile reports a failed final rename through Android's log. Do
        // not publish a handle until the complete replacement is readable.
        if(!file.openRead().use {it.readBytes().contentEquals(bytes)}) throw IOException("Draft checkpoint was not published")
        return project.id.takeIf {valid()}
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
        deleteCheckpoint(id)
        for(kind in listOf("stories","audiobook")) {
            val active=try {activeReference(kind)} catch(_:IOException) {continue}
            if(active==id) activeFile(kind).delete()
        }
    }

    fun deleteOwned(id: String,owner: Owner,isUnreferenced: ()->Boolean): Boolean = synchronized(ioLock) {
        if(!owns(owner)||!isUnreferenced()) return@synchronized false
        // Unknown pointer metadata cannot authorize deleting a possibly
        // referenced checkpoint. There is no directory/age-based sweep.
        val active=try {listOfNotNull(activeReference("stories"),activeReference("audiobook"))}
        catch(_:IOException) {return@synchronized false}
        if(id in active) return@synchronized false
        val referenced=synchronized(ownerLock) {
            val prefix=root.absoluteFile.toPath().normalize().toString()+":"
            owners.filterKeys {it.startsWith(prefix)}.values.any {id in it.references()}
        }
        if(referenced) return@synchronized false
        deleteCheckpoint(id)
        true
    }

    private fun deleteCheckpoint(id: String) {
        val file=checkpoint(id)
        file.delete()
        val base=file.baseFile
        if(listOf(base,File(base.path+".new"),File(base.path+".bak")).any {it.exists()}) {
            throw IOException("Draft checkpoint could not be removed")
        }
    }
}
