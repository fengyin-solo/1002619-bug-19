<template>
  <section class="page" data-module="cargo">
    <header class="page-head">
      <div>
        <h2>货物装卸管理</h2>
        <p class="page-desc">填报单可暂存，缺字段逐项说明；装机前按货邮编号去重，先落库为准，三处台账口径一致。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出装卸明细</button>
      </div>
    </header>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span v-if="tab.count" class="tab-badge">{{ tab.count }}</span>
      </button>
    </nav>

    <!-- 填报单 -->
    <div v-show="activeTab === 'form'" class="tab-panel">
      <form class="cargo-form" @submit.prevent="submitLoading">
        <div class="form-grid">
          <label v-for="field in formFields" :key="field" class="form-field">
            <span>{{ field }}<em v-if="requiredFields.includes(field)" class="req">*</em></span>
            <input
              v-model="form[field]"
              :type="field === '板箱数量' ? 'number' : 'text'"
              :min="field === '板箱数量' ? 1 : undefined"
              :placeholder="`请输入${field}`"
              @input="onFormInput"
            />
          </label>
        </div>

        <p v-if="missingHints.length" class="missing-hint">
          还缺必填项：<em v-for="m in missingHints" :key="m">{{ m }}</em>
        </p>
        <p v-else class="ok-text">必填项已齐，可提交装机</p>

        <div class="form-actions">
          <button class="btn" type="button" @click="saveDraft">暂存草稿</button>
          <button class="btn primary" type="submit" :disabled="submitting">
            {{ submitting ? '装机中…' : '装机提交' }}
          </button>
          <button v-if="cabinStatus === 'error'" class="btn ghost" type="button" @click="queryCabin">
            重试舱位查询
          </button>
          <span v-if="cabinStatus === 'loading'" class="spinner">舱位查询中…</span>
          <span v-if="cabinStatus === 'ok'" class="ok-text">舱位已分配：{{ form['舱位分配'] }}</span>
        </div>

        <p v-if="formError" class="error-text">{{ formError }}</p>
        <p v-if="draftSaved" class="ok-text">草稿已暂存，刷新页面不丢</p>
      </form>
    </div>

    <!-- 待处理清单 -->
    <div v-show="activeTab === 'pending'" class="tab-panel">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in listColumns" :key="column">{{ column }}</th>
            <th>缺项说明</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in pendingRows"
            :key="String(row.id)"
            :class="{ 'row-incomplete': rowMissing(row).length }"
          >
            <td v-for="column in listColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>
              <span v-if="rowMissing(row).length" class="missing-hint">缺：{{ rowMissing(row).join('、') }}</span>
              <span v-else class="ok-text">资料齐全</span>
            </td>
            <td class="row-actions">
              <button class="link" type="button" @click="continueFill(row)">继续填报</button>
            </td>
          </tr>
          <tr v-if="!pendingRows.length">
            <td :colspan="listColumns.length + 2" class="empty-state">暂无待处理货邮</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>待处理 {{ pendingRows.length }} 条，装机数 {{ pendingCount }} 板</span>
      </footer>
    </div>

    <!-- 装卸明细 -->
    <div v-show="activeTab === 'details'" class="tab-panel">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in listColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in detailRows" :key="String(row.id)">
            <td v-for="column in listColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!detailRows.length">
            <td :colspan="listColumns.length" class="empty-state">暂无装卸明细</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>已装机 {{ detailRows.length }} 条，装机数合计 {{ totalCount }} 板</span>
      </footer>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/cargo'
const formFields = ["货邮编号", "对应航班", "货物类型", "总重吨位", "板箱数量", "装卸班组", "舱位分配"]
const requiredFields = ["货邮编号", "板箱数量", "舱位分配"]
const listColumns = ["货邮编号", "对应航班", "货物类型", "总重吨位", "板箱数量", "装卸班组", "舱位分配", "装卸状态"]

const tabs = computed(() => [
  { key: 'form', label: '填报单', count: 0 },
  { key: 'pending', label: '待处理清单', count: pendingRows.value.length },
  { key: 'details', label: '装卸明细', count: detailRows.value.length },
])

const activeTab = ref<'form' | 'pending' | 'details'>('form')
const form = reactive<Record<string, string>>({})
const batchId = ref('')
const baseVersion = ref<number | null>(null)
const cabinStatus = ref<'idle' | 'loading' | 'error' | 'ok'>('idle')
const submitting = ref(false)
const formError = ref('')
const draftSaved = ref(false)
const pendingRows = ref<Row[]>([])
const detailRows = ref<Row[]>([])

const STORAGE_KEY = 'cargo-draft'

function newBatchId(): string {
  return `batch-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function emptyForm(): Record<string, string> {
  const result: Record<string, string> = {}
  formFields.forEach((field) => { result[field] = '' })
  return result
}

function resetForm() {
  Object.assign(form, emptyForm())
  batchId.value = newBatchId()
  baseVersion.value = null
  cabinStatus.value = 'idle'
  formError.value = ''
  draftSaved.value = false
  refreshMissing()
}

function missingOf(values: Record<string, unknown>, fields: string[]): string[] {
  return fields.filter((field) => {
    const value = values[field]
    return value === undefined || value === null || String(value).trim() === ''
  })
}

const missingHints = ref<string[]>([])
function refreshMissing() {
  missingHints.value = missingOf(form, requiredFields)
}

function onFormInput() {
  draftSaved.value = false
  refreshMissing()
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(form))
  } catch {
    /* 本地存储不可用时不影响填报 */
  }
}

async function loadDraft() {
  // 优先服务端暂存，本地草稿兜底，重新打开页面草稿还在
  try {
    const response = await request(`${ENDPOINT}/draft`)
    if (response.ok) {
      const draft = await response.json()
      if (draft && Object.keys(draft).length) {
        formFields.forEach((field) => {
          form[field] = draft[field] !== undefined && draft[field] !== null ? String(draft[field]) : ''
        })
        refreshMissing()
        return
      }
    }
  } catch {
    /* 服务端取不到时退回本地草稿 */
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const draft = JSON.parse(raw) as Record<string, string>
      formFields.forEach((field) => { form[field] = draft[field] ?? '' })
      refreshMissing()
    }
  } catch {
    /* 本地草稿也没有就算了 */
  }
}

async function saveDraft() {
  formError.value = ''
  try {
    const response = await request(`${ENDPOINT}/draft`, {
      method: 'POST',
      body: JSON.stringify({ values: { ...form } }),
    })
    if (!response.ok) throw new Error('暂存失败，请稍后重试')
    draftSaved.value = true
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(form))
    } catch {
      /* 本地存储不可用时不影响暂存 */
    }
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '暂存失败，请稍后重试'
  }
}

async function queryCabin() {
  cabinStatus.value = 'loading'
  formError.value = ''
  try {
    const flight = encodeURIComponent(form['对应航班'] ?? '')
    const response = await request(`${ENDPOINT}/cabin-allocation?flight=${flight}`)
    if (!response.ok) {
      cabinStatus.value = 'error'
      return
    }
    const payload = await response.json()
    form['舱位分配'] = payload['舱位分配'] ?? ''
    cabinStatus.value = 'ok'
    refreshMissing()
  } catch {
    cabinStatus.value = 'error'
  }
}

async function submitLoading() {
  formError.value = ''
  // 提交前快照：失败时退回原填报内容，板箱数量不能丢
  const snapshot = { ...form }
  submitting.value = true
  try {
    const response = await request(`${ENDPOINT}/loading`, {
      method: 'POST',
      body: JSON.stringify({
        values: { ...form },
        batch_id: batchId.value,
        base_version: baseVersion.value,
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      Object.assign(form, snapshot)
      refreshMissing()
      formError.value = payload?.message ?? '装机提交失败，已退回原填报内容'
      return
    }
    resetForm()
    await Promise.all([reloadPending(), reloadDetails()])
  } catch (error) {
    Object.assign(form, snapshot)
    refreshMissing()
    formError.value = error instanceof Error
      ? `${error.message}，已退回原填报内容`
      : '装机提交失败，已退回原填报内容'
  } finally {
    submitting.value = false
  }
}

function rowMissing(row: Row): string[] {
  return missingOf(row, requiredFields)
}

function continueFill(row: Row) {
  formFields.forEach((field) => {
    form[field] = row[field] !== undefined && row[field] !== null ? String(row[field]) : ''
  })
  baseVersion.value = typeof row.version === 'number' ? row.version : null
  batchId.value = newBatchId()
  activeTab.value = 'form'
  refreshMissing()
}

async function reloadPending() {
  try {
    const response = await request(`${ENDPOINT}/pending?size=200`)
    if (response.ok) {
      const payload = await response.json()
      pendingRows.value = payload.items ?? []
    }
  } catch {
    /* 保留上一次的清单，不覆盖 */
  }
}

async function reloadDetails() {
  try {
    const response = await request(`${ENDPOINT}/details?size=200`)
    if (response.ok) {
      const payload = await response.json()
      detailRows.value = payload.items ?? []
    }
  } catch {
    /* 保留上一次的明细，不覆盖 */
  }
}

const pendingCount = computed(() =>
  pendingRows.value.reduce((sum, row) => sum + (Number(row['板箱数量']) || 0), 0),
)
const totalCount = computed(() =>
  detailRows.value.reduce((sum, row) => sum + (Number(row['板箱数量']) || 0), 0),
)

function switchTab(tab: string) {
  activeTab.value = tab as typeof activeTab.value
  if (tab === 'pending') void reloadPending()
  if (tab === 'details') void reloadDetails()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(() => {
  resetForm()
  void loadDraft()
  void reloadPending()
  void reloadDetails()
})
</script>

<style scoped>
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}
.tab {
  border: 1px solid transparent;
  border-bottom: none;
  background: none;
  padding: 8px 14px;
  cursor: pointer;
  font-size: 13px;
  color: var(--muted);
  border-radius: 6px 6px 0 0;
}
.tab.active {
  background: #fff;
  border-color: var(--border);
  color: var(--brand);
  font-weight: 600;
}
.tab-badge {
  display: inline-block;
  min-width: 18px;
  padding: 0 5px;
  margin-left: 4px;
  background: var(--brand);
  color: #fff;
  border-radius: 9px;
  font-size: 11px;
  line-height: 16px;
  text-align: center;
}
.tab-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.form-field span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.form-field .req {
  color: #b42318;
  margin-left: 2px;
}
.form-field input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.missing-hint {
  color: #b42318;
  font-size: 12px;
  margin: 10px 0 0;
}
.missing-hint em {
  font-style: normal;
  margin-right: 8px;
}
.ok-text {
  color: #1a7f37;
  font-size: 12px;
  margin: 10px 0 0;
}
.form-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}
.spinner {
  color: var(--muted);
  font-size: 12px;
}
.row-incomplete {
  background: #f8fafc;
  color: #94a3b8;
}
.row-incomplete td {
  color: #94a3b8;
}
</style>
