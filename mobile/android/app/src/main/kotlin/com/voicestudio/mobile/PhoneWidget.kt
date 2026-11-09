package com.voicestudio.mobile

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.RemoteViews
import com.voicestudio.mobile.l10n.L10n
import java.util.Locale

/** Explicit taps only: the widget opens an editor and never starts the microphone. */
class PhoneWidget: AppWidgetProvider() {
    override fun onUpdate(context: Context,manager: AppWidgetManager,ids: IntArray) {
        val l=L10n.load(context,Locale.getDefault().toLanguageTag())
        ids.forEach {id ->
            val views=RemoteViews(context.packageName,R.layout.phone_widget)
            listOf("speak" to R.id.widget_speak,"dictate" to R.id.widget_dictate).forEach {(route,view) ->
                views.setTextViewText(view,l.t("mobile.integration.shortcut_$route"))
                val intent=Intent(Intent.ACTION_VIEW,Uri.parse("voicestudio://workspace/$route"),context,MainActivity::class.java)
                views.setOnClickPendingIntent(view,PendingIntent.getActivity(context,view,intent,PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            }
            manager.updateAppWidget(id,views)
        }
    }
}
