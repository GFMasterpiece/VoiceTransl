<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import { profileHost, type ProviderProfile } from '../stores/providerProfiles'
const props = defineProps<{ id: string; profile?: ProviderProfile; providerType?: string; snapshot?: boolean; label?: string; loading?: boolean }>()
const issue = computed(() => !props.id ? t('profiles.notChosen') : !props.profile ? props.loading ? t('profiles.loading') : t('profiles.missing') : !props.profile.enabled ? t('profiles.disabled')
  : props.providerType && props.profile.provider_type !== props.providerType ? t('profiles.mismatch') : '')
</script>
<template>
  <div class="provider-target" :data-provider-target="snapshot ? undefined : id" :data-provider-snapshot-target="snapshot ? id : undefined">
    <strong>{{ profile?.display_name || label || id || t('profiles.choose') }}</strong>
    <span v-if="loading && !profile">{{ t('profiles.loading') }}</span><span v-else>{{ profileHost(profile) || t('profiles.unknownTarget') }} · {{ profile?.region || t('profiles.unknownRegion') }}</span>
    <small v-if="id">{{ id }}</small><small v-if="issue" class="node-field-error">{{ issue }}</small>
  </div>
</template>
