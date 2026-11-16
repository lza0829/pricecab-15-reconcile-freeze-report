<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const clock = ref('')
const batches = ref([])
const msg = ref('')
const err = ref('')
const result = ref(null)
const policy = ref('void')
const policyLabel = ref('')

const policyOptions = [
  { key: 'void', label: '旧报告作废' },
  { key: 'archive', label: '旧报告只读存档' },
]

async function refresh() {
  const c = await api.clock()
  clock.value = c.business_clock || ''
  batches.value = await api.batches()
  const p = await api.reportPolicy()
  policy.value = p.policy
  policyLabel.value = p.label
}

async function saveClock() {
  msg.value = ''
  err.value = ''
  try {
    await api.setClock(clock.value)
    msg.value = '业务钟已更新'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function applyDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  try {
    result.value = await api.applyDue(policy.value)
    if (result.value.applied) {
      const rs = result.value.reports
      msg.value = `已应用 ${result.value.ticket_count} 张改价单（批次 ${result.value.batch_no}）；` +
        (rs && rs.retired_count
          ? `旧报告按「${rs.policy_label}」处理 ${rs.retired_count} 份：${rs.retired_report_nos.join('、')}`
          : '无旧报告需要处理')
    } else {
      msg.value = result.value.message
    }
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>生效应用</h1>
    <p>拨动业务钟后，点「应用到期」将 effective_at ≤ 业务钟的改价单写入现行价签。</p>
  </section>

  <div class="panel">
    <h2>业务钟</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>业务钟 (UTC)</label>
        <input v-model="clock" />
      </div>
      <button class="btn ghost" type="button" @click="saveClock">保存业务钟</button>
    </div>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
  </div>

  <div class="panel">
    <h2>应用到期</h2>
    <p class="msg">
      应用与「冻结报告生成」互斥：报告钉扎中途点应用会被拒绝，应用进行中也不能生成报告。
      应用改写价签后，旧冻结报告按下方策略处理，不会再冒充现行结论。
    </p>
    <div class="row">
      <div class="field" style="flex:1">
        <label>本次应用对旧报告的处理（当前默认：{{ policyLabel }}）</label>
        <select v-model="policy">
          <option v-for="p in policyOptions" :key="p.key" :value="p.key">{{ p.label }}</option>
        </select>
      </div>
      <button class="btn warn" type="button" @click="applyDue">应用到期</button>
    </div>
    <pre v-if="result" style="margin-top:12px;font-size:0.82rem;color:var(--muted);white-space:pre-wrap">{{ JSON.stringify(result, null, 2) }}</pre>
  </div>

  <div class="panel">
    <h2>应用批次</h2>
    <table>
      <thead>
        <tr><th>批次</th><th>业务钟</th><th>改价单数</th><th>明细行</th><th>状态</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.clock_at }}</td>
          <td>{{ b.ticket_count }}</td>
          <td>{{ b.line_count }}</td>
          <td><span class="tag ok">{{ b.status }}</span></td>
          <td>{{ b.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
