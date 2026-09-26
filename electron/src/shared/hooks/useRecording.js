/**
 * useRecording — microphone recording with auto-cleanup via the backend.
 *
 * Extracted from App.jsx to reduce its useState/useRef count.
 */
import { useEffect, useState, useRef } from 'react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { cleanAudio as apiCleanAudio } from '../api/system';
import { micErrorMessage } from '../utils/micError';
import { checkMicrophone } from '../utils/permissions';
import { showMicDeniedGuide } from '../utils/micDeniedToast';
import { startMicCapture } from '../utils/aec/micCapture';
import { encodeWav } from '../utils/audioTrim';
import { audioFormatForMimeType, startSupportedMediaRecorder } from '../utils/mediaRecorder';
import {
  buildAudioInputConstraints,
  createInputLevelStore,
  listAudioInputs,
  startInputLevelMonitor,
} from '../utils/audioInput';

function concatFrames(frames) {
  const length = frames.reduce((total, frame) => total + frame.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const frame of frames) {
    samples.set(frame, offset);
    offset += frame.length;
  }
  return samples;
}

export default function useRecording(ingestRefAudio) {
  const { t } = useTranslation();
  const [isRecording, setIsRecording] = useState(false);
  const [isStartingRecording, setIsStartingRecording] = useState(false);
  const [isCleaning, setIsCleaning] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [audioInputs, setAudioInputs] = useState([]);
  const [selectedAudioInputId, setSelectedAudioInputId] = useState('');
  const [channelMode, setChannelMode] = useState('auto');
  const inputLevelStoreRef = useRef(null);
  if (!inputLevelStoreRef.current) inputLevelStoreRef.current = createInputLevelStore();
  const mediaRecorderRef = useRef(null);
  const recordingChunksRef = useRef([]);
  const recordingTimerRef = useRef(null);
  const stopLevelMonitorRef = useRef(null);
  const recordingStartRef = useRef(false);

  const refreshAudioInputs = async () => {
    try {
      const inputs = await listAudioInputs();
      setAudioInputs(inputs);
      setSelectedAudioInputId((current) =>
        current && !inputs.some((device) => device.deviceId === current) ? '' : current,
      );
    } catch {
      setAudioInputs([]);
    }
  };

  const stopLevelMonitor = () => {
    stopLevelMonitorRef.current?.();
    stopLevelMonitorRef.current = null;
    inputLevelStoreRef.current.set(0);
  };

  useEffect(() => {
    void refreshAudioInputs();
    const mediaDevices = navigator.mediaDevices;
    mediaDevices?.addEventListener?.('devicechange', refreshAudioInputs);
    return () => {
      mediaDevices?.removeEventListener?.('devicechange', refreshAudioInputs);
      stopLevelMonitorRef.current?.();
      clearInterval(recordingTimerRef.current);
    };
  }, []);

  const startRecording = async () => {
    if (recordingStartRef.current || isRecording) return;
    recordingStartRef.current = true;
    setIsStartingRecording(true);
    try {
      // Pre-flight: an OS-denied mic grant means getUserMedia can only throw an
      // opaque NotAllowedError. Skip it and show the guided path instead.
      if ((await checkMicrophone()) === 'denied') {
        showMicDeniedGuide(t);
        return;
      }
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia(
          buildAudioInputConstraints(selectedAudioInputId, channelMode),
        );
        void refreshAudioInputs();
        stopLevelMonitor();
        try {
          stopLevelMonitorRef.current = startInputLevelMonitor(
            stream,
            inputLevelStoreRef.current.set,
          );
        } catch {
          // Level feedback is optional; recording must still work when a webview
          // exposes getUserMedia without a complete Web Audio implementation.
          stopLevelMonitorRef.current = null;
        }
        recordingChunksRef.current = [];
        setRecordingTime(0);

        const finishRecording = async (blob, extension) => {
          clearInterval(recordingTimerRef.current);
          stopLevelMonitor();
          stream.getTracks().forEach((t) => t.stop());
          if (blob.size < 1000) {
            toast.error(t('recording.too_short', { defaultValue: 'Recording too short' }));
            return;
          }

          // Send to backend for denoising
          setIsCleaning(true);
          try {
            const formData = new FormData();
            formData.append('audio', blob, `recording.${extension}`);
            const res = await apiCleanAudio(formData);

            const cleanBlob = await res.blob();
            const cleanFilename = res.headers.get('X-Clean-Filename') || 'recording_clean.wav';
            const cleanFile = new File([cleanBlob], cleanFilename, { type: 'audio/wav' });

            await ingestRefAudio(cleanFile);
            toast.success(
              t('recording.cleaned_loaded', { defaultValue: 'Recording cleaned & loaded!' }),
            );
          } catch (e) {
            // Fallback: use raw recording without denoising
            const rawFile = new File([blob], `recording.${extension}`, { type: blob.type });
            await ingestRefAudio(rawFile);
            toast.success(
              t('recording.loaded_raw', {
                defaultValue: 'Recording loaded (raw — denoising unavailable)',
              }),
            );
          } finally {
            setIsCleaning(false);
          }
        };

        let recordingFormat = { mimeType: 'audio/webm', extension: 'webm' };
        const supported = startSupportedMediaRecorder(stream, {
          onData: (e) => {
            if (e.data.size > 0) {
              if (e.data.type) recordingFormat = audioFormatForMimeType(e.data.type);
              recordingChunksRef.current.push(e.data);
            }
          },
          onStop: () => {
            const blob = new Blob(recordingChunksRef.current, { type: recordingFormat.mimeType });
            void finishRecording(blob, recordingFormat.extension);
          },
        });
        if (supported) {
          const { recorder, mimeType, extension } = supported;
          recordingFormat = { mimeType, extension };
          mediaRecorderRef.current = recorder;
        } else {
          // Some Linux WebKitGTK builds expose MediaRecorder but reject every
          // codec/constructor. Web Audio is still available, so record PCM
          // and wrap it in a portable WAV instead of failing the microphone.
          const frames = [];
          const actualChannels = Number(stream.getAudioTracks?.()[0]?.getSettings?.().channelCount);
          const pcmChannels =
            channelMode === 'mono'
              ? 1
              : channelMode === 'stereo'
                ? 2
                : Math.max(1, Math.min(2, actualChannels || 1));
          const stopCapture = await startMicCapture(stream, (frame) => frames.push(frame.slice()), {
            sampleRate: 16000,
            channels: pcmChannels,
          });
          const controller = {
            state: 'recording',
            stop() {
              if (controller.state === 'inactive') return;
              controller.state = 'inactive';
              void Promise.resolve(stopCapture())
                .catch(() => {})
                .then(() => {
                  const wav = encodeWav(
                    concatFrames(frames),
                    stopCapture.sampleRate || 16000,
                    stopCapture.channels || pcmChannels,
                  );
                  return finishRecording(new Blob([wav], { type: 'audio/wav' }), 'wav');
                });
            },
          };
          mediaRecorderRef.current = controller;
        }
        setIsRecording(true);

        // Timer
        const st = Date.now();
        recordingTimerRef.current = setInterval(() => {
          setRecordingTime(((Date.now() - st) / 1000).toFixed(1));
        }, 100);
      } catch (e) {
        stopLevelMonitor();
        stream?.getTracks().forEach((track) => track.stop());
        // Same actionable mapping as the dictation pill: denied → per-OS
        // settings hint; otherwise no-device / device-busy / generic (#323).
        toast.error(micErrorMessage(t, e), { duration: 6000 });
      }
    } finally {
      recordingStartRef.current = false;
      setIsStartingRecording(false);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    stopLevelMonitor();
    setIsRecording(false);
  };

  return {
    isRecording,
    isStartingRecording,
    isCleaning,
    recordingTime,
    audioInputs,
    selectedAudioInputId,
    setSelectedAudioInputId,
    channelMode,
    setChannelMode,
    inputLevelStore: inputLevelStoreRef.current,
    startRecording,
    stopRecording,
  };
}
