import { ref } from 'vue'

import { fetchHidden, fetchLikes, fetchStorage, rateTrack, unmuteArtist } from '../api'
import type { HiddenArtist, LibraryStorage, LibraryTrack } from '../types'

// Данные экрана «Моя музыка»: лайки (страницами), скрытое, место на сервере.
export function useLibrary() {
  const likes = ref<LibraryTrack[]>([])
  const total = ref(0)
  const hiddenTracks = ref<LibraryTrack[]>([])
  const hiddenArtists = ref<HiddenArtist[]>([])
  const storage = ref<LibraryStorage | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const query = ref('')

  async function loadLikes(reset = true): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const page = await fetchLikes(reset ? 0 : likes.value.length, query.value.trim())
      likes.value = reset ? page.tracks : [...likes.value, ...page.tracks]
      total.value = page.total
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function loadHidden(): Promise<void> {
    try {
      const hidden = await fetchHidden()
      hiddenTracks.value = hidden.tracks
      hiddenArtists.value = hidden.artists
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function loadStorage(): Promise<void> {
    storage.value = await fetchStorage().catch(() => null)
  }

  async function refresh(): Promise<void> {
    await Promise.all([loadLikes(), loadHidden(), loadStorage()])
  }

  async function unlike(track: LibraryTrack): Promise<void> {
    likes.value = likes.value.filter((t) => t.ref !== track.ref)
    total.value = Math.max(0, total.value - 1)
    await rateTrack('none', track.ref).catch(() => loadLikes())
    void loadStorage()
  }

  async function restoreTrack(track: LibraryTrack): Promise<void> {
    hiddenTracks.value = hiddenTracks.value.filter((t) => t.ref !== track.ref)
    await rateTrack('none', track.ref).catch(() => loadHidden())
    void loadHidden()
  }

  async function restoreArtist(artist: HiddenArtist): Promise<void> {
    hiddenArtists.value = hiddenArtists.value.filter((a) => a.artist !== artist.artist)
    await unmuteArtist(artist.artist).catch(() => undefined)
    void loadHidden()
  }

  return {
    likes,
    total,
    hiddenTracks,
    hiddenArtists,
    storage,
    loading,
    error,
    query,
    loadLikes,
    refresh,
    unlike,
    restoreTrack,
    restoreArtist,
  }
}
