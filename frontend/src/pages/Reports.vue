<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const list = ref(null)
const detail = ref(null)
const note = ref('')
const holdMs = ref(0)
const msg = ref('')
const err = ref('')
const busy = ref(false)
const policyDraft = ref('')

const policyOptions = [
  { key: 'void', label: '旧报告作废', desc: '应用后旧报告作废：查询标明作废，不得再当现行结论，须用新钟或新水位重切。' },
  { key: 'archive', label: '旧报告只读存档', desc: '应用后旧报告转只读存档：仍可打开复查，但标「非现行」；现行结论只来自最新未作废报告。' },
]

const current = computed(() =>
  list.value?.reports?.find((r) => r.report_no === list.value.current_report_no) || null,
)

async function refresh() {
  list.value = await api.reports()
  if (!policyDraft.value) policyDraft.value = list.value.policy
}

async function openReport(reportNo) {
  err.value = ''
  try {
    detail.value = await api.report(reportNo)
  } catch (e) {
    err.value = e.message
  }
}

async function generate() {
  msg.value = ''
  err.value = ''
  busy.value = true
  try {
    const r = await api.createReport(note.value, holdMs.value)
    msg.value = `已生成冻结报告 ${r.report_no}（业务钟 ${r.business_clock}，水位 #${r.history_watermark}）`
    note.value = ''
    holdMs.value = 0
    await refresh()
    detail.value = r
  } catch (e) {
    err.value = e.message
  } finally {
    busy.value = false
  }
}

async function savePolicy() {
  msg.value = ''
  err.value = ''
  try {
    await api.setReportPolicy(policyDraft.value)
    msg.value = '旧报告命运策略已保存'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

function statusClass(status) {
  if (status === 'active') return 'ok'
  if (status === 'voided') return 'bad'
  if (status === 'archived') return 'warn'
  return 'muted'
}

function rowClass(status) {
  if (status === 'match') return 'ok'
  if (status === 'mismatch') return 'bad'
  return 'muted'
}

onMounted(() =>
  refresh().catch((e) => {
    err.value = e.message
  }),
)
</script>

<template>
  <section class="hero">
    <h1>对账冻结报告</h1>
    <p>
      生成时钉扎业务钟与价格历史水位。报告内一致 / 不一致集合与分桶计数固定存档，可随时复查；
      生成与新应用互斥，钉扎中途不会被改价单改写。
    </p>
    <div class="chain">
      <div class="step"><strong>1 钉扎</strong>业务钟 + 价格水位</div>
      <div class="step"><strong>2 存档</strong>一致 / 不一致集合可复查</div>
      <div class="step"><strong>3 应用后</strong>旧报告按策略作废或只读存档</div>
    </div>
  </section>

  <p v-if="err" class="msg err">{{ err }}</p>
  <p v-if="msg" class="msg ok">{{ msg }}</p>

  <div class="panel">
    <h2>生成冻结报告</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>备注（可选）</label>
        <input v-model="note" placeholder="例如：午市结束切账" />
      </div>
      <div class="field">
        <label>钉扎演示停留 (ms)</label>
        <input v-model.number="holdMs" type="number" min="0" max="5000" step="100" />
      </div>
      <button class="btn" type="button" :disabled="busy" @click="generate">
        {{ busy ? '钉扎中…' : '生成冻结报告' }}
      </button>
    </div>
    <p class="msg">
      报告以当前业务钟为窗口，价格只取水位之内的历史记录；生成期间改价应用会因冲突被拒绝。
    </p>
  </div>

  <div class="panel">
    <h2>旧报告命运策略</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>应用到期改写价签后，旧报告如何处理</label>
        <select v-model="policyDraft">
          <option v-for="p in policyOptions" :key="p.key" :value="p.key">{{ p.label }}</option>
        </select>
      </div>
      <button class="btn ghost" type="button" @click="savePolicy">保存策略</button>
    </div>
    <p v-if="list" class="msg">
      当前策略：<strong>{{ list.label }}</strong>。{{ policyOptions.find((p) => p.key === list.policy)?.desc }}
    </p>
    <p v-if="current" class="msg">
      现行结论来自 <strong>{{ current.report_no }}</strong>；最新未作废报告之外的报告均标「非现行」。
    </p>
    <p v-else-if="list" class="msg err">当前没有未作废的现行报告，需重新生成冻结报告。</p>
  </div>

  <div v-if="list" class="panel">
    <h2>报告列表</h2>
    <table>
      <thead>
        <tr>
          <th>报告号</th><th>业务钟</th><th>价格水位</th><th>销售笔数</th>
          <th>一致 / 不一致 / 缺价</th><th>状态</th><th>生成</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in list.reports" :key="r.report_no">
          <td>{{ r.report_no }}</td>
          <td>{{ r.business_clock }}</td>
          <td>#{{ r.history_watermark }}</td>
          <td>{{ r.sale_count }}</td>
          <td>{{ r.match_count }} / {{ r.mismatch_count }} / {{ r.missing_count }}</td>
          <td>
            <span class="tag" :class="statusClass(r.status)">{{ r.status_label }}</span>
          </td>
          <td>{{ r.created_at }}</td>
          <td><button class="btn ghost" type="button" @click="openReport(r.report_no)">查看</button></td>
        </tr>
        <tr v-if="!list.reports.length">
          <td colspan="8" class="msg">尚无冻结报告。上方生成第一份。</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div v-if="detail" class="panel">
    <h2>{{ detail.report_no }} · {{ detail.status_label }}</h2>
    <p class="msg">{{ detail.notice }}</p>
    <p class="msg">{{ detail.pin_statement }}</p>
    <p v-if="detail.retired_reason" class="msg">
      旧报告命运：{{ detail.retire_policy_label }}；原因：{{ detail.retired_reason }}
      <template v-if="detail.retired_by_batch_no">（应用批次 {{ detail.retired_by_batch_no }}）</template>
    </p>
    <p v-if="detail.note" class="msg">备注：{{ detail.note }}</p>
    <p v-if="detail.basis_warning" class="msg err">{{ detail.basis_warning }}</p>

    <p v-if="detail.clock_snapshot" class="msg">
      钉扎钟快照：应用到期改价单 {{ detail.clock_snapshot.applied_due_ticket_count }} 张
      <template v-if="detail.clock_snapshot.unapplied_due_ticket_nos?.length">
        ｜ 到期未应用：{{ detail.clock_snapshot.unapplied_due_ticket_nos.join('、') }}
      </template>
      <template v-if="detail.clock_warning"> ｜ <span class="msg err">{{ detail.clock_warning }}</span></template>
    </p>

    <div class="grid">
      <div class="stat"><div class="label">销售笔数</div><div class="value">{{ detail.counts.sale_count }}</div></div>
      <div class="stat"><div class="label">一致</div><div class="value">{{ detail.counts.match_count }}</div></div>
      <div class="stat"><div class="label">不一致</div><div class="value">{{ detail.counts.mismatch_count }}</div></div>
      <div class="stat"><div class="label">缺价</div><div class="value">{{ detail.counts.missing_count }}</div></div>
    </div>
    <p class="msg">
      复查重放：<span class="tag" :class="detail.replay_verified ? 'ok' : 'bad'">
        {{ detail.replay_verified ? '明细与存档一致' : `${detail.replay_mismatch_count} 行漂移` }}
      </span>
      <span class="tag" :class="detail.counts_replay_verified ? 'ok' : 'bad'" style="margin-left:8px">
        {{ detail.counts_replay_verified ? '分桶计数回溯一致' : '计数不一致' }}
      </span>
      （按同一水位重算，不读现行价签）
    </p>

    <table>
      <thead>
        <tr>
          <th>销售号</th><th>SKU</th><th>成交时刻</th><th>成交价</th><th>当时价签</th>
          <th>价签来源</th><th>差额</th><th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in detail.rows" :key="r.sale_no">
          <td>{{ r.sale_no }}</td>
          <td>{{ r.sku_code }}</td>
          <td>{{ r.sold_at }}</td>
          <td>{{ r.unit_price }}</td>
          <td>{{ r.expected_price ?? '—' }}</td>
          <td>{{ r.source_ticket_no || '初始' }} · #{{ r.price_entry_id ?? '—' }}</td>
          <td>{{ r.delta ?? '—' }}</td>
          <td><span class="tag" :class="rowClass(r.status)">{{ r.status }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
