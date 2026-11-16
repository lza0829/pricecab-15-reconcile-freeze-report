<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const data = ref(null)
const err = ref('')

async function refresh() {
  try {
    data.value = await api.reconcile()
  } catch (e) {
    err.value = e.message
  }
}

onMounted(refresh)

function tagClass(status) {
  if (status === 'match') return 'ok'
  if (status === 'mismatch') return 'bad'
  return 'muted'
}
</script>

<template>
  <section class="hero">
    <h1>销售对账（现场预览）</h1>
    <p>
      用销售夹具的成交单价，与「成交时刻」对应的价签历史比对。此列表每次现场重算——
      新应用一发生结论就会变，<strong>不是冻结报告</strong>，不能当作可复查的固定结论。
    </p>
  </section>
  <p v-if="err" class="msg err">{{ err }}</p>

  <div v-if="data" class="panel">
    <p class="msg err">
      挥发性列表：每次调用重新读取现行价格历史，无钉扎、无水位，不得冒充冻结报告。
    </p>
    <p class="msg">
      现行冻结报告：<strong>{{ data.current_report_no || '无' }}</strong>
      ｜ 业务钟：{{ data.business_clock || '未设置' }}
      ｜ 需要可复查结论请到
      <router-link to="/reports"><u>冻结报告</u></router-link>。
    </p>
  </div>

  <div v-if="data" class="grid">
    <div class="stat"><div class="label">销售笔数</div><div class="value">{{ data.sale_count }}</div></div>
    <div class="stat"><div class="label">一致</div><div class="value">{{ data.match_count }}</div></div>
    <div class="stat"><div class="label">不一致</div><div class="value">{{ data.mismatch_count }}</div></div>
    <div class="stat"><div class="label">缺价</div><div class="value">{{ data.missing_count }}</div></div>
  </div>

  <div v-if="data" class="panel">
    <h2>明细</h2>
    <table>
      <thead>
        <tr>
          <th>销售号</th><th>SKU</th><th>成交时刻</th><th>成交价</th>
          <th>当时价签</th><th>差额</th><th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in data.rows" :key="r.sale_no">
          <td>{{ r.sale_no }}</td>
          <td>{{ r.sku_code }}</td>
          <td>{{ r.sold_at }}</td>
          <td>{{ r.unit_price }}</td>
          <td>{{ r.expected_price ?? '—' }}</td>
          <td>{{ r.delta ?? '—' }}</td>
          <td><span class="tag" :class="tagClass(r.status)">{{ r.status }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
