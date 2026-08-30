<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import MetricCard from './components/MetricCard.vue'
import SlotPicker from './components/SlotPicker.vue'
import SlotRow from './components/SlotRow.vue'
import { fetchState, requestRefresh, submitRoom, submitSelection } from './api.js'

const STATE_POLL_MS = 1000

// __MST_VERSION__ is injected at build time from archipelago.json's world_version -- see
// vite.config.js's `define`. Not a ref: it's fixed for the lifetime of this bundle, no reason to
// make it reactive.
const mstVersion = __MST_VERSION__

const serverState = ref({
  room_loaded: false,
  selection_confirmed: false,
  available_slots: [],
  selected_slot_ids: null,
  error: null,
  dashboard: null,
})
// Remembers only the room text itself, in this browser's localStorage -- never the slot selection,
// and never auto-connects on its own. Pre-fills the field so re-entering the same room doesn't
// mean retyping/re-pasting the UUID every single time, while still requiring the user to
// consciously hit Load each session (the actual privacy-relevant step -- reading another player's
// progress -- stays an explicit action every time, exactly as before).
const ROOM_STORAGE_KEY = 'mst_last_room'
const roomText = ref('')
try {
  roomText.value = localStorage.getItem(ROOM_STORAGE_KEY) || ''
} catch {
  // localStorage can throw (private browsing, disabled site data, ...) -- fall back to empty,
  // same as before this feature existed.
}
const localSubmitError = ref(null)

const pickerOpen = ref(false)
let pickerAutoShownForThisLoad = false

const gameFilter = ref('')
const compatFilter = ref('')
const sortMode = ref('name_asc')
const requireInLogic = ref(false)
const requireOutOfLogic = ref(false)
const requireHinted = ref(false)
const requireGoMode = ref(false)

const now = ref(Date.now())
let pollTimer = null
let clockTimer = null

// Shown as a full-page overlay once polling fails a few times in a row -- distinct from the
// regular error banner (which is a backend-*reported* problem, like a bad room id): this means the
// backend process itself is unreachable, most likely because the launcher window was closed, so
// every action in this tab would be futile until it's running again. A couple of misses are
// tolerated first (STARTUP_GRACE_MISSES) so a single slow/late first request right after the page
// loads doesn't immediately flash this.
const connectionLost = ref(false)
const CONNECTION_LOST_AFTER_MISSES = 3
let consecutiveMisses = 0

onMounted(() => {
  refreshState()
  pollTimer = setInterval(refreshState, STATE_POLL_MS)
  clockTimer = setInterval(() => (now.value = Date.now()), 1000)
})
onUnmounted(() => {
  clearInterval(pollTimer)
  clearInterval(clockTimer)
})

async function refreshState() {
  try {
    serverState.value = await fetchState()
    consecutiveMisses = 0
    connectionLost.value = false
  } catch {
    consecutiveMisses += 1
    if (consecutiveMisses >= CONNECTION_LOST_AFTER_MISSES) connectionLost.value = true
  }
}

// Auto-open the slot picker once per freshly-loaded room, the moment its slot list actually has
// something in it -- mirrors the old Kivy app's set_available_slots()/_startup_picker_shown gate,
// so the user narrows down the list *before* the (potentially slow) full computation pass starts.
watch(
  () => serverState.value.available_slots.length,
  (len) => {
    if (len > 0 && !serverState.value.selection_confirmed && !pickerAutoShownForThisLoad) {
      pickerAutoShownForThisLoad = true
      pickerOpen.value = true
    }
  },
)

async function loadRoom() {
  const text = roomText.value.trim()
  if (!text) return
  pickerAutoShownForThisLoad = false
  localSubmitError.value = null
  try {
    await submitRoom(text)
    await refreshState()
    try {
      localStorage.setItem(ROOM_STORAGE_KEY, text)
    } catch {
      // non-fatal -- the room still loaded fine, it just won't be pre-filled next time.
    }
  } catch (e) {
    localSubmitError.value = e.message
  }
}

async function applySelection(slotIds) {
  pickerOpen.value = false
  await submitSelection(slotIds)
  await refreshState()
}

// The picker's own contract is simple (an array of ids that start checked -- see its docstring);
// the "null means all slots, including future ones" meaning `selected_slot_ids` carries on the
// backend only applies *after* a selection has actually been confirmed at least once, so it's
// resolved here rather than inside SlotPicker itself: never-confirmed -> start with nothing
// checked (so the user actively narrows a possibly huge list instead of deselecting ~200 slots),
// previously-confirmed-as-"all" -> reopening should still show everything checked.
const pickerInitialSelection = computed(() => {
  if (!serverState.value.selection_confirmed) return []
  if (serverState.value.selected_slot_ids === null) {
    return serverState.value.available_slots.map((s) => s.slot_id)
  }
  return serverState.value.selected_slot_ids
})

const dashboard = computed(() => serverState.value.dashboard)
const displayError = computed(() => localSubmitError.value || serverState.value.error)

const subtitle = computed(() => {
  if (!dashboard.value) return 'Enter a room above to get started.'
  const secs = Math.max(0, Math.round((now.value - new Date(dashboard.value.generated_at).getTime()) / 1000))
  return `Updated ${secs}s ago`
})

const availableGames = computed(() => {
  if (!dashboard.value) return []
  return [...new Set(dashboard.value.slots.map((s) => s.game))].sort((a, b) => a.localeCompare(b))
})

const SORT_FNS = {
  name_asc: (a, b) => a.slot_name.localeCompare(b.slot_name),
  name_desc: (a, b) => b.slot_name.localeCompare(a.slot_name),
  open_asc: (a, b) => (a.in_logic_open ?? 0) - (b.in_logic_open ?? 0),
  open_desc: (a, b) => (b.in_logic_open ?? 0) - (a.in_logic_open ?? 0),
  hinted_asc: (a, b) => a.hinted_in_logic - b.hinted_in_logic,
  hinted_desc: (a, b) => b.hinted_in_logic - a.hinted_in_logic,
}

const visibleSlots = computed(() => {
  if (!dashboard.value) return []
  const filtered = dashboard.value.slots.filter((s) => {
    if (gameFilter.value && s.game !== gameFilter.value) return false
    if (compatFilter.value && s.compatibility !== compatFilter.value) return false
    if (requireInLogic.value && !(s.in_logic_open > 0)) return false
    if (requireOutOfLogic.value && !(s.out_of_logic_open > 0)) return false
    if (requireHinted.value && !(s.hinted_in_logic > 0)) return false
    if (requireGoMode.value && !s.no_progression_needed) return false
    return true
  })
  return [...filtered].sort(SORT_FNS[sortMode.value])
})
</script>

<template>
  <div class="app-root">
    <div class="room-row">
      <span class="room-label">Room</span>
      <input
        v-model="roomText"
        class="room-input"
        type="text"
        placeholder="Room or tracker URL, or just its UUID"
        @keyup.enter="loadRoom"
      />
      <button class="pill-button active" @click="loadRoom">Load</button>
    </div>

    <div v-if="displayError" class="error-banner">{{ displayError }}</div>

    <div class="header-row">
      <div class="header-text">
        <h1>Multi-slot tracker <span class="version-tag">v{{ mstVersion }}</span></h1>
        <p class="subtitle">{{ subtitle }}</p>
      </div>
      <div class="header-actions">
        <button class="pill-button" @click="pickerOpen = true">Select slots...</button>
        <button class="icon-button" title="Refresh now" @click="requestRefresh">&#x21bb;</button>
      </div>
    </div>

    <div class="filters-group">
      <div class="filter-row">
        <select v-model="gameFilter" class="pill-select">
          <option value="">All games</option>
          <option v-for="g in availableGames" :key="g" :value="g">{{ g }}</option>
        </select>
        <select v-model="compatFilter" class="pill-select">
          <option value="">All tiers</option>
          <option value="slot_data">Slot data</option>
          <option value="yaml_required">Yaml required</option>
          <option value="unknown_game">Unknown game</option>
        </select>
        <select v-model="sortMode" class="pill-select">
          <option value="name_asc">Sort: Name (A-Z)</option>
          <option value="name_desc">Sort: Name (Z-A)</option>
          <option value="open_asc">Sort: Checks in logic (low-high)</option>
          <option value="open_desc">Sort: Checks in logic (high-low)</option>
          <option value="hinted_asc">Sort: Hinted in logic (low-high)</option>
          <option value="hinted_desc">Sort: Hinted in logic (high-low)</option>
        </select>
      </div>
      <div class="filter-row">
        <button class="pill-button" :class="{ active: requireInLogic }" @click="requireInLogic = !requireInLogic">
          Has in-logic checks
        </button>
        <button class="pill-button" :class="{ active: requireOutOfLogic }" @click="requireOutOfLogic = !requireOutOfLogic">
          Has out-of-logic checks
        </button>
        <button class="pill-button" :class="{ active: requireHinted }" @click="requireHinted = !requireHinted">
          Has hinted checks
        </button>
        <button class="pill-button" :class="{ active: requireGoMode }" @click="requireGoMode = !requireGoMode">
          Go mode
        </button>
      </div>
    </div>

    <div class="metrics-row">
      <MetricCard label="Slots watched" :value="dashboard ? dashboard.slots.length : '-'" />
      <MetricCard label="Hinted in logic" :value="dashboard ? dashboard.total_hinted_in_logic : '-'" />
      <MetricCard label="Checks open" :value="dashboard ? dashboard.total_open : '-'" />
      <MetricCard label="Of which in logic" :value="dashboard ? dashboard.total_in_logic : '-'" />
      <MetricCard label="Restricted" :value="dashboard ? dashboard.restricted_count : '-'" />
    </div>

    <div class="slot-list scrollbar-thin">
      <template v-for="(slot, i) in visibleSlots" :key="slot.slot_id">
        <hr v-if="i > 0" class="row-divider" />
        <SlotRow :slot="slot" />
      </template>
      <div v-if="dashboard && visibleSlots.length === 0" class="slot-list-empty">
        No slots match the current filters.
      </div>
    </div>

    <SlotPicker
      v-if="pickerOpen"
      :available="serverState.available_slots"
      :initial-selection="pickerInitialSelection"
      @apply="applySelection"
      @cancel="pickerOpen = false"
    />

    <div v-if="connectionLost" class="connection-lost-backdrop">
      <div class="connection-lost-box">
        <h2>Lost connection to Multi Slot Tracker</h2>
        <p>
          The app this page talks to isn't responding -- it was most likely closed. Everything on
          this page is frozen on its last known state until it's running again.
        </p>
        <p class="connection-lost-hint">
          Reopen the Multi Slot Tracker launcher, then reload this page.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-root {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-height: 100vh;
}

.room-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.room-label {
  color: var(--text-muted);
  flex-shrink: 0;
}

.room-input {
  flex: 1;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  color: var(--text);
}

.error-banner {
  background: var(--error-bg);
  color: var(--error-fg);
  border-radius: var(--radius);
  padding: 10px 14px;
  font-size: 14px;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.header-text h1 {
  margin: 0;
  font-size: 24px;
}

.version-tag {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-muted);
  vertical-align: middle;
}

.subtitle {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 14px;
}

.header-actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.icon-button {
  border-radius: 50%;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--accent-strong);
  width: 40px;
  height: 40px;
  font-size: 18px;
  line-height: 1;
}

.filters-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.filter-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pill-select {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--accent-strong);
  padding: 8px 14px;
  font-size: 13px;
}

.metrics-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.slot-list {
  flex: 1;
  overflow-y: auto;
}

.row-divider {
  border: none;
  border-top: 1px solid var(--border);
  margin: 0;
}

.slot-list-empty {
  color: var(--text-muted);
  text-align: center;
  padding: 40px 0;
}

.connection-lost-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200; /* above the slot picker (100) -- a dead backend outranks any in-progress dialog */
  padding: 24px;
}

.connection-lost-box {
  background: var(--bg-elevated);
  border: 1px solid var(--error-fg);
  border-radius: var(--radius);
  max-width: 440px;
  padding: 28px;
  text-align: center;
}

.connection-lost-box h2 {
  margin: 0 0 12px;
  font-size: 19px;
  color: var(--error-fg);
}

.connection-lost-box p {
  margin: 0 0 10px;
  color: var(--text);
  line-height: 1.5;
}

.connection-lost-box .connection-lost-hint {
  color: var(--text-muted);
  font-size: 13px;
}
</style>
