<template>
  <section class="page" data-module="cargo">
    <header class="page-head">
      <div>
        <h2>货物装卸管理</h2>
        <p class="page-desc">装机填报单可暂存、缺字段灰显提示；填报单、待处理清单与装卸明细的装机数同源一致。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出货物装卸清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>
    <p v-if="summaryError" class="error-text">{{ summaryError }}</p>

    <section class="panel">
      <div class="panel-head">
        <h3>装机填报单</h3>
        <span class="panel-note">
          已装机 {{ summary.loaded }} 票
          <template v-if="draftSavedAt"> · 草稿暂存于 {{ draftSavedAt }}</template>
        </span>
      </div>
      <p class="panel-note">带 * 的字段缺了也能暂存，灰显行会说明缺哪一项；提交装机时才会逐行校验。</p>

      <table class="data-table form-table">
        <thead>
          <tr>
            <th>货邮编号 *</th>
            <th>对应航班 *</th>
            <th>货物类型 *</th>
            <th>总重吨位</th>
            <th>板箱数量 *</th>
            <th>装卸班组</th>
            <th>舱位分配</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(row, index) in formRows" :key="index">
            <tr
              :class="{
                'row-incomplete': formRowMissing(row).length > 0,
                'row-failed': failedRows.has(index + 1),
              }"
            >
              <td><input v-model="row.货邮编号" placeholder="如 CARG-0001" /></td>
              <td><input v-model="row.对应航班" placeholder="如 CA1234" /></td>
              <td><input v-model="row.货物类型" placeholder="普货 / 邮件" /></td>
              <td><input v-model="row.总重吨位" placeholder="如 1.2 吨" /></td>
              <td><input v-model="row.板箱数量" placeholder="整数，只增不减" /></td>
              <td><input v-model="row.装卸班组" placeholder="班组名称" /></td>
              <td>
                <input v-model="row.舱位分配" placeholder="可先查舱位" />
                <button
                  class="link"
                  type="button"
                  :disabled="allocState[index]?.loading"
                  @click="queryAllocation(index)"
                >
                  {{ allocState[index]?.loading ? '查询中…' : '查询舱位' }}
                </button>
                <div v-if="allocState[index]?.error" class="alloc-error">
                  <span>{{ allocState[index].error }}</span>
                  <button class="link" type="button" @click="queryAllocation(index)">重试</button>
                </div>
              </td>
              <td><button class="link" type="button" @click="removeRow(index)">删除行</button></td>
            </tr>
            <tr v-if="formRowMissing(row).length" class="missing-hint-row">
              <td colspan="8">第 {{ index + 1 }} 行缺：{{ formRowMissing(row).join('、') }}（可暂存，提交装机前需补齐）</td>
            </tr>
          </template>
          <tr v-if="!formRows.length">
            <td colspan="8" class="empty-state">填报单暂无行，点击下方「添加一行」开始填报</td>
          </tr>
        </tbody>
      </table>

      <ul v-if="failures.length" class="failure-list">
        <li v-for="failure in failures" :key="failure.row">
          第 {{ failure.row }} 行<template v-if="failure.货邮编号">（{{ failure.货邮编号 }}）</template>：{{ failure.reason }}
        </li>
      </ul>

      <div class="form-actions">
        <button class="btn" type="button" @click="addRow">添加一行</button>
        <button class="btn" type="button" @click="saveDraft">暂存草稿</button>
        <button class="btn primary" type="button" :disabled="submitting" @click="submitLoad">
          {{ submitting ? '装机提交中…' : '提交装机' }}
        </button>
        <span v-if="formMessage" class="success-text">{{ formMessage }}</span>
        <span v-if="formError" class="error-text">{{ formError }}</span>
        <span v-if="draftError" class="error-text">{{ draftError }}</span>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>待处理清单</h3>
        <span class="panel-note">待处理 {{ summary.pending }} 条 · 已装机 {{ summary.loaded }} 票</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>货邮编号</th>
            <th>对应航班</th>
            <th>板箱数量</th>
            <th>装卸状态</th>
            <th>缺项说明</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in pendingRows"
            :key="String(row.id)"
            :class="{ 'row-incomplete': (row.missing_fields ?? []).length > 0 }"
          >
            <td>{{ row.货邮编号 ?? '—' }}</td>
            <td>{{ row.对应航班 ?? '—' }}</td>
            <td>{{ row.板箱数量 ?? '—' }}</td>
            <td>{{ row.status ?? '—' }}</td>
            <td>{{ row.missing_text || '—' }}</td>
          </tr>
          <tr v-if="!pendingRows.length">
            <td colspan="5" class="empty-state">暂无待处理的货邮任务</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ pendingTotal }} 条待处理</span>
        <span v-if="pendingError" class="error-text">{{ pendingError }}</span>
      </footer>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>装卸明细</h3>
        <span class="panel-note">共 {{ total }} 条 · 已装机 {{ summary.loaded }} 票</span>
      </div>

      <form class="filter-bar" @submit.prevent="loadList">
        <label class="filter-item">
          <span>货邮编号</span>
          <input v-model="filters.keyword" placeholder="按货邮编号检索" />
        </label>
        <label class="filter-item">
          <span>装卸状态</span>
          <select v-model="filters.status">
            <option value="">全部状态</option>
            <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>缺项说明</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="String(row.id)"
            :class="{ 'row-incomplete': (row.missing_fields ?? []).length > 0 }"
          >
            <td v-for="column in columns" :key="column">{{ displayCell(row, column) }}</td>
            <td>{{ row.missing_text || '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 2" class="empty-state">暂无货物装卸数据，可先在上方填报单登记</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ total }} 条货物装卸记录 · 已装机 {{ summary.loaded }} 票</span>
        <span v-if="listError" class="error-text">{{ listError }}</span>
      </footer>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

interface FormRow {
  货邮编号: string
  对应航班: string
  货物类型: string
  总重吨位: string
  板箱数量: string
  装卸班组: string
  舱位分配: string
}

interface Summary {
  total: number
  pending: number
  loaded: number
  counts: Record<string, number>
}

interface LoadFailure {
  row: number
  货邮编号?: string
  reason: string
}

interface LoadResult {
  ok: boolean
  message: string
  loaded: Row[]
  failures: LoadFailure[]
  form: { rows: Row[] } | null
}

const ENDPOINT = '/api/cargo'
const columns = ['货邮编号', '对应航班', '货物类型', '总重吨位', '板箱数量', '装卸班组', '舱位分配', '装卸状态']
const actions = ['安排装卸', '开始装机', '确认入库']
const statuses = ['待装卸', '装卸中', '已装机', '已入库']
const FORM_FIELDS = ['货邮编号', '对应航班', '货物类型', '总重吨位', '板箱数量', '装卸班组', '舱位分配'] as const

const summary = ref<Summary>({ total: 0, pending: 0, loaded: 0, counts: {} })
const pendingRows = ref<Row[]>([])
const pendingTotal = ref(0)
const rows = ref<Row[]>([])
const total = ref(0)

const formRows = ref<FormRow[]>([emptyFormRow()])
const failures = ref<LoadFailure[]>([])
const allocState = ref<Record<number, { loading: boolean; error: string }>>({})
const draftSavedAt = ref('')
const submitting = ref(false)

const formMessage = ref('')
const formError = ref('')
const draftError = ref('')
const summaryError = ref('')
const pendingError = ref('')
const listError = ref('')

const filters = ref({ keyword: '', status: '' })

const stats = computed(() => [
  { label: '待装卸货邮', value: summary.value.counts['待装卸'] ?? 0 },
  { label: '装卸中货邮', value: summary.value.counts['装卸中'] ?? 0 },
  { label: '已装机货邮', value: summary.value.counts['已装机'] ?? 0 },
  { label: '已入库货邮', value: summary.value.counts['已入库'] ?? 0 },
])

const failedRows = computed(() => new Set(failures.value.map((failure) => failure.row)))

function emptyFormRow(): FormRow {
  return { 货邮编号: '', 对应航班: '', 货物类型: '', 总重吨位: '', 板箱数量: '', 装卸班组: '', 舱位分配: '' }
}

function normalizeFormRow(row: Row): FormRow {
  const text = (value: unknown) => (value === null || value === undefined ? '' : String(value))
  return {
    货邮编号: text(row.货邮编号),
    对应航班: text(row.对应航班),
    货物类型: text(row.货物类型),
    总重吨位: text(row.总重吨位),
    板箱数量: text(row.板箱数量),
    装卸班组: text(row.装卸班组),
    舱位分配: text(row.舱位分配),
  }
}

function parseUld(value: string): number | null {
  const textValue = value.trim()
  if (!textValue) return null
  const num = Number(textValue)
  if (!Number.isFinite(num)) return null
  const count = Math.trunc(num)
  return count >= 0 ? count : null
}

/** 与服务端同一份缺项口径：缺货邮编号、对应航班、货物类型或板箱数量的行灰显。 */
function formRowMissing(row: FormRow): string[] {
  const missing: string[] = []
  if (!row.货邮编号.trim()) missing.push('货邮编号')
  if (!row.对应航班.trim()) missing.push('对应航班')
  if (!row.货物类型.trim()) missing.push('货物类型')
  if (parseUld(row.板箱数量) === null) missing.push('板箱数量')
  return missing
}

function displayCell(row: Row, column: string): string | number {
  if (column === '装卸状态') return row.status ?? '—'
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : value
}

function addRow() {
  formRows.value.push(emptyFormRow())
}

function removeRow(index: number) {
  formRows.value.splice(index, 1)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function queryAllocation(index: number) {
  const row = formRows.value[index]
  if (!row) return
  const keyword = row.货邮编号.trim() || row.对应航班.trim()
  if (!keyword) {
    allocState.value[index] = { loading: false, error: '先填货邮编号或对应航班，再查舱位分配' }
    return
  }
  allocState.value[index] = { loading: true, error: '' }
  try {
    const response = await request(`${ENDPOINT}/allocation?keyword=${encodeURIComponent(keyword)}`)
    if (!response.ok) {
      let detail = `舱位分配查询失败（${response.status}）`
      try {
        const body = await response.json()
        if (body?.detail) detail = String(body.detail)
      } catch {
        // 保留默认说明
      }
      throw new Error(detail)
    }
    const payload = await response.json()
    // 查不到时不走这里；查到时回填，已填内容不覆盖
    if (!row.舱位分配.trim()) row.舱位分配 = String(payload.舱位分配 ?? '')
    if (!row.货邮编号.trim() && payload.货邮编号) row.货邮编号 = String(payload.货邮编号)
    if (!row.对应航班.trim() && payload.对应航班) row.对应航班 = String(payload.对应航班)
    allocState.value[index] = { loading: false, error: '' }
  } catch (error) {
    // 查询失败只影响这一行的舱位栏，已填内容原样保留，并给出重试
    allocState.value[index] = {
      loading: false,
      error: error instanceof Error ? error.message : '舱位分配查询失败，可重试',
    }
  }
}

async function saveDraft() {
  formMessage.value = ''
  formError.value = ''
  try {
    const response = await request(`${ENDPOINT}/draft`, {
      method: 'PUT',
      body: JSON.stringify({ values: { rows: formRows.value } }),
    })
    if (!response.ok) throw new Error('草稿暂存失败，请稍后重试')
    const payload = await response.json()
    draftSavedAt.value = payload.saved_at ?? ''
    formMessage.value = '草稿已暂存，重新打开页面还会保留'
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '草稿暂存失败'
  }
}

async function submitLoad() {
  formMessage.value = ''
  formError.value = ''
  failures.value = []
  // 完全空白的行不上送，避免误报缺项
  const submitRows = formRows.value
    .map((row) => ({ ...row }))
    .filter((row) => FORM_FIELDS.some((field) => row[field].trim() !== ''))
  if (!submitRows.length) {
    formError.value = '填报单为空，请先填写至少一行再提交装机'
    return
  }
  submitting.value = true
  try {
    const response = await request(`${ENDPOINT}/load`, {
      method: 'POST',
      body: JSON.stringify({ values: { rows: submitRows } }),
    })
    const result = (await response.json()) as LoadResult
    if (!response.ok || !result.ok) {
      // 装机失败：用服务端退回的原填报内容恢复表单，一行都不丢
      if (result.form?.rows?.length) {
        formRows.value = result.form.rows.map((row) => normalizeFormRow(row))
      }
      failures.value = result.failures ?? []
      formError.value = result.message || '装机未落库，请按提示修正后重新提交'
      return
    }
    formRows.value = [emptyFormRow()]
    draftSavedAt.value = ''
    formMessage.value = result.message || '装机完成'
    await Promise.all([loadSummary(), loadPending(), loadList()])
  } catch (error) {
    // 网络异常时表单内容没动过，直接提示即可
    formError.value = error instanceof Error ? error.message : '装机提交失败，填报内容已保留'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  listError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) throw new Error('货物装卸动作未生效，请稍后重试')
    await Promise.all([loadSummary(), loadPending(), loadList()])
  } catch (error) {
    listError.value = error instanceof Error ? error.message : '货物装卸操作失败'
  }
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void loadList()
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) throw new Error('装机统计读取失败')
    summary.value = await response.json()
    summaryError.value = ''
  } catch (error) {
    summaryError.value = error instanceof Error ? error.message : '装机统计读取失败'
  }
}

async function loadPending() {
  try {
    const response = await request(`${ENDPOINT}/pending`)
    if (!response.ok) throw new Error('待处理清单读取失败')
    const payload = await response.json()
    pendingRows.value = payload.items ?? []
    pendingTotal.value = payload.total ?? pendingRows.value.length
    pendingError.value = ''
  } catch (error) {
    pendingError.value = error instanceof Error ? error.message : '待处理清单读取失败'
  }
}

async function loadList() {
  try {
    const query = new URLSearchParams()
    if (filters.value.keyword.trim()) query.set('keyword', filters.value.keyword.trim())
    if (filters.value.status) query.set('status', filters.value.status)
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('货邮任务列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    listError.value = ''
  } catch (error) {
    listError.value = error instanceof Error ? error.message : '货物装卸列表读取失败'
  }
}

async function loadDraft() {
  try {
    const response = await request(`${ENDPOINT}/draft`)
    if (!response.ok) throw new Error('草稿读取失败')
    const payload = await response.json()
    const draftRows = Array.isArray(payload.rows) ? payload.rows : []
    if (draftRows.length) {
      formRows.value = draftRows.map((row: Row) => normalizeFormRow(row))
      draftSavedAt.value = payload.saved_at ?? ''
    }
  } catch (error) {
    draftError.value = error instanceof Error ? error.message : '草稿读取失败，可直接重新填写'
  }
}

onMounted(() => {
  // 各区块独立加载、各自报错，任何一处失败都不会让整页卡住
  void Promise.all([loadSummary(), loadPending(), loadList(), loadDraft()])
})
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 6px;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.panel-note {
  color: var(--muted);
  font-size: 12px;
  margin: 4px 0 8px;
}
.form-table input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 13px;
}
.form-table .link {
  font-size: 12px;
  white-space: nowrap;
}
.row-incomplete td {
  background: #f1f5f9;
  color: var(--muted);
}
.row-incomplete input {
  background: #f8fafc;
  color: var(--muted);
}
.row-failed td {
  background: #fef3f2;
}
.missing-hint-row td {
  background: #f8fafc;
  color: #b42318;
  font-size: 12px;
  padding: 4px 10px;
}
.form-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-top: 10px;
  flex-wrap: wrap;
}
.failure-list {
  margin: 10px 0 0;
  padding: 8px 12px;
  border: 1px solid #fecdca;
  background: #fffbfa;
  border-radius: 6px;
  color: #b42318;
  font-size: 12px;
  list-style: none;
}
.alloc-error {
  color: #b42318;
  font-size: 12px;
  margin-top: 4px;
  display: flex;
  gap: 6px;
  align-items: center;
}
.success-text {
  color: #067647;
  font-size: 12px;
}
.filter-item select {
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 13px;
}
</style>
