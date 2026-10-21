// one livekit room for the interview page. the room object is mutable, so this keeps
// a render counter and reads participants and tracks from the room on each render

import {
  ConnectionState,
  DisconnectReason,
  type LocalAudioTrack,
  type LocalVideoTrack,
  type RemoteParticipant,
  type RemoteTrack,
  Room,
  RoomEvent,
  Track,
} from 'livekit-client'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { CallConnection } from '@/api/types'

export type CallState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'ended'

export interface Media {
  audio: LocalAudioTrack | null
  video: LocalVideoTrack | null
  micOn: boolean
  camOn: boolean
}

export interface Person {
  identity: string
  name: string
  video: RemoteTrack | null
  micMuted: boolean
  camOff: boolean
  speaking: boolean
}

function toState(s: ConnectionState): CallState {
  if (s === ConnectionState.Connected) return 'connected'
  if (s === ConnectionState.Connecting) return 'connecting'
  if (s === ConnectionState.Disconnected) return 'ended'
  return 'reconnecting'
}

function person(p: RemoteParticipant): Person {
  const cam = p.getTrackPublication(Track.Source.Camera)
  const mic = p.getTrackPublication(Track.Source.Microphone)
  return {
    identity: p.identity,
    name: p.name || '',
    video: cam && !cam.isMuted ? (cam.track ?? null) : null,
    micMuted: !mic || mic.isMuted,
    camOff: !cam || cam.isMuted || !cam.track,
    speaking: p.isSpeaking,
  }
}

export function useCall(onData?: (topic: string | undefined, message: unknown) => void) {
  const roomRef = useRef<Room | null>(null)
  const onDataRef = useRef(onData)
  onDataRef.current = onData
  const [, setTick] = useState(0)
  const [state, setState] = useState<CallState>('idle')
  const [endReason, setEndReason] = useState<DisconnectReason | undefined>()
  const [audioBlocked, setAudioBlocked] = useState(false)
  const bump = useCallback(() => setTick((t) => t + 1), [])

  const join = useCallback(
    async (conn: CallConnection, media: Media) => {
      await roomRef.current?.disconnect()
      const room = new Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room
      setEndReason(undefined)
      room
        .on(RoomEvent.ConnectionStateChanged, (s) => {
          setState(toState(s))
          bump()
        })
        .on(RoomEvent.Disconnected, (reason) => {
          setEndReason(reason)
          setState('ended')
          // remote audio elements were added to the page by attach()
          document.querySelectorAll('audio[data-lk-remote]').forEach((el) => el.remove())
        })
        .on(RoomEvent.TrackSubscribed, (track) => {
          if (track.kind === Track.Kind.Audio) {
            const el = track.attach()
            el.dataset.lkRemote = 'true'
            document.body.appendChild(el)
          }
          bump()
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((el) => el.remove())
          bump()
        })
        .on(RoomEvent.AudioPlaybackStatusChanged, () => setAudioBlocked(!room.canPlaybackAudio))
        .on(RoomEvent.DataReceived, (payload, _from, _kind, topic) => {
          try {
            onDataRef.current?.(topic, JSON.parse(new TextDecoder().decode(payload)))
          } catch {
            // not ours, ignore
          }
        })
      for (const event of [
        RoomEvent.ParticipantConnected,
        RoomEvent.ParticipantDisconnected,
        RoomEvent.TrackMuted,
        RoomEvent.TrackUnmuted,
        RoomEvent.ActiveSpeakersChanged,
        RoomEvent.LocalTrackPublished,
        RoomEvent.LocalTrackUnpublished,
      ])
        room.on(event, bump)

      setState('connecting')
      try {
        await room.connect(conn.url, conn.token)
        if (media.audio) {
          await room.localParticipant.publishTrack(media.audio)
          if (!media.micOn) await media.audio.mute()
        }
        if (media.video) {
          if (media.camOn) await room.localParticipant.publishTrack(media.video)
          else media.video.stop()
        }
        // the join click counts as the gesture browsers want before playing sound
        await room.startAudio().catch(() => undefined)
        setAudioBlocked(!room.canPlaybackAudio)
      } catch (e) {
        await room.disconnect()
        setState('ended')
        throw e
      }
      bump()
    },
    [bump],
  )

  const leave = useCallback(async () => {
    await roomRef.current?.disconnect()
  }, [])

  useEffect(
    () => () => {
      void roomRef.current?.disconnect()
    },
    [],
  )

  const room = roomRef.current
  const local = room?.localParticipant
  const camPub = local?.getTrackPublication(Track.Source.Camera)

  return {
    state,
    endReason,
    // left on purpose, as opposed to a dropped connection or a closed room
    leftByUs: endReason === DisconnectReason.CLIENT_INITIATED,
    roomClosed: endReason === DisconnectReason.ROOM_DELETED,
    replaced: endReason === DisconnectReason.DUPLICATE_IDENTITY,
    audioBlocked,
    people: room ? Array.from(room.remoteParticipants.values()).map(person) : [],
    micOn: !!local?.isMicrophoneEnabled,
    camOn: !!local?.isCameraEnabled,
    selfVideo: camPub && !camPub.isMuted ? (camPub.track ?? null) : null,
    selfSpeaking: !!local?.isSpeaking,
    join,
    leave,
    toggleMic: async () => {
      await local?.setMicrophoneEnabled(!local.isMicrophoneEnabled)
      bump()
    },
    toggleCam: async () => {
      await local?.setCameraEnabled(!local.isCameraEnabled)
      bump()
    },
    startAudio: async () => {
      await room?.startAudio()
      setAudioBlocked(!room?.canPlaybackAudio)
    },
  }
}

export type Call = ReturnType<typeof useCall>
