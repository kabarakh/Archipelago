<script setup>
import { computed } from 'vue'

const props = defineProps({
  slot: { type: Object, required: true },
})

const COMPAT_LABEL = { slot_data: 'Slot data', yaml_required: 'Yaml required', unknown_game: 'Unknown game' }
const COMPAT_ROLE = { slot_data: 'positive', yaml_required: 'caution', unknown_game: 'neutral' }

const compatLabel = computed(() => COMPAT_LABEL[props.slot.compatibility] ?? props.slot.compatibility)
const compatRole = computed(() => COMPAT_ROLE[props.slot.compatibility] ?? 'neutral')

// Only slot_data/yaml_required tiers actually regenerate a world to compute logic -- unknown_game
// never computes anything at all, so the "counts can be slightly off" caveat doesn't apply to it.
const REGEN_CAVEAT =
  "Computed by regenerating this slot's world, not by reading the room's actual generated data. " +
  'For most games that\'s fine, but a few games make random choices during generation that decide ' +
  'which locations even exist -- for those, counts can be slightly off from what a live-connected ' +
  "client would show. Not a bug; see the app's docs for why."
const compatTitle = computed(() =>
  props.slot.compatibility === 'unknown_game' ? undefined : REGEN_CAVEAT,
)

const openCount = computed(() => props.slot.total_locations - props.slot.checked)
const progressPct = computed(() =>
  props.slot.total_locations > 0 ? Math.round((props.slot.checked / props.slot.total_locations) * 100) : 0,
)
</script>

<template>
  <div class="slot-row">
    <div class="slot-row-top">
      <div class="slot-name-game">
        <span class="slot-name">{{ slot.slot_name }}</span>
        <span class="slot-game">{{ slot.game }}</span>
      </div>
      <div class="slot-badges">
        <span v-if="slot.source === 'live'" class="badge accent">Live</span>
        <span class="badge" :class="compatRole" :title="compatTitle">{{ compatLabel }}</span>
      </div>
    </div>

    <template v-if="slot.error">
      <div class="slot-error">{{ slot.error }}</div>
    </template>
    <template v-else>
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: progressPct + '%' }" />
      </div>
      <div class="slot-row-bottom">
        <span class="slot-caption">{{ slot.checked }} / {{ slot.total_locations }} checks done</span>
        <div class="slot-pills">
          <span class="badge positive">In logic {{ slot.in_logic_open ?? 0 }}</span>
          <span class="badge caution">
            Out of logic {{ slot.out_of_logic_open === null ? 'n/a' : slot.out_of_logic_open }}
          </span>
          <span v-if="slot.hinted_in_logic > 0" class="badge hinted">Hinted in logic {{ slot.hinted_in_logic }}</span>
          <span v-if="slot.no_progression_needed" class="badge accent">Go mode</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.slot-row {
  padding: 10px 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.slot-row-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.slot-name-game {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.slot-name {
  font-weight: 600;
  white-space: nowrap;
}

.slot-game {
  color: var(--text-muted);
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.slot-badges {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.progress-track {
  height: 6px;
  border-radius: 3px;
  background: var(--surface-high);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
}

.slot-row-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.slot-caption {
  color: var(--text-muted);
  font-size: 13px;
}

.slot-pills {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.slot-error {
  color: var(--error-fg);
  font-size: 13px;
}
</style>
