<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  available: { type: Array, required: true }, // [{slot_id, slot_name, game}]
  // The ids that should start checked -- App.vue resolves the backend's "null means all slots"
  // convention before it ever reaches this component, so this is always a plain array here.
  initialSelection: { type: Array, default: () => [] },
})
const emit = defineEmits(['apply', 'cancel'])

const search = ref('')
// Seeded once, when this component is created -- App.vue mounts a fresh SlotPicker instance each
// time the dialog opens (v-if), so this naturally re-seeds per opening without needing a reactive
// watch. A watch on props.available in particular was wrong here: the parent polls /api/state
// every second, and each poll produces a brand-new array (even with identical content), which
// made a `watch` on it fire on every tick and silently reset the user's in-progress selection back
// to "everything" a moment after they'd deselected something -- reported live as "deselect visible
// looks like it undoes itself after a second".
//
// initialSelection === null means "nothing confirmed yet" (the very first, auto-opened picker for
// a freshly loaded room) -- start with nothing checked so the user actively picks slots rather than
// having to deselect ~200 of them to narrow down a big room; a *re-opened* picker still restores
// whatever was actually confirmed before (initialSelection is the explicit array in that case).
const selection = ref(new Set(props.initialSelection))
const canApply = computed(() => selection.value.size > 0)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return props.available
  return props.available.filter(
    (s) => s.slot_name.toLowerCase().includes(q) || s.game.toLowerCase().includes(q),
  )
})

function toggle(slotId) {
  const next = new Set(selection.value)
  if (next.has(slotId)) next.delete(slotId)
  else next.add(slotId)
  selection.value = next
}

function selectVisible() {
  const next = new Set(selection.value)
  for (const s of filtered.value) next.add(s.slot_id)
  selection.value = next
}

function deselectVisible() {
  const next = new Set(selection.value)
  for (const s of filtered.value) next.delete(s.slot_id)
  selection.value = next
}

function apply() {
  const all = props.available.length > 0 && selection.value.size === props.available.length
  emit('apply', all ? null : Array.from(selection.value))
}
</script>

<template>
  <div class="picker-backdrop" @click.self="emit('cancel')">
    <div class="picker-dialog">
      <h2>Select slots to watch</h2>
      <input v-model="search" class="picker-search" type="text" placeholder="Filter by name or game" />

      <div class="picker-actions-row">
        <button class="pill-button" @click="selectVisible">Select visible</button>
        <button class="pill-button" @click="deselectVisible">Deselect visible</button>
      </div>

      <div class="picker-list scrollbar-thin">
        <label v-for="s in filtered" :key="s.slot_id" class="picker-item">
          <span class="picker-item-text">{{ s.slot_name }} &ndash; {{ s.game }}</span>
          <input type="checkbox" :checked="selection.has(s.slot_id)" @change="toggle(s.slot_id)" />
        </label>
        <div v-if="filtered.length === 0" class="picker-empty">No slots match.</div>
      </div>

      <div class="picker-footer">
        <span v-if="!canApply" class="picker-hint">Select at least one slot to continue</span>
        <button class="pill-button" @click="emit('cancel')">Cancel</button>
        <button class="pill-button active" :disabled="!canApply" @click="apply">Apply</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.picker-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 8vh;
  z-index: 100;
}

.picker-dialog {
  background: var(--bg-elevated);
  border-radius: var(--radius);
  border: 1px solid var(--border);
  width: min(560px, 92vw);
  max-height: 78vh;
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 14px;
}

.picker-dialog h2 {
  margin: 0;
  font-size: 20px;
  text-align: center;
}

.picker-search {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  color: var(--text);
}

.picker-actions-row {
  display: flex;
  gap: 8px;
}

.picker-list {
  overflow-y: auto;
  border-top: 1px solid var(--border);
  padding-top: 4px;
  flex: 1;
  min-height: 120px;
}

.picker-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  cursor: pointer;
}

.picker-item-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-empty {
  color: var(--text-muted);
  text-align: center;
  padding: 24px 0;
}

.picker-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.picker-hint {
  color: var(--text-muted);
  font-size: 13px;
  margin-right: auto;
}

.pill-button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
