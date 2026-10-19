/**
 * 字幕插件前端共享模块。
 * 独立 ESM 文件通过相对导入复用，loader 只加载 index.mjs。
 */

let host = null;

export const API_BASE = '/plugin-framework/plugins/subtitle/api';

const CSS = `
.subtitle-plugin { display:flex; flex-direction:column; gap:16px; padding:1.25rem; background:hsl(var(--background)); min-height:100%; }
.subtitle-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.subtitle-grid { display:grid; grid-template-columns:minmax(300px,400px) minmax(0,1fr); gap:16px; align-items:start; }
.subtitle-grid > * { min-width:0; }
.subtitle-plugin .n-card { min-width:0; }
.subtitle-muted { color:hsl(var(--muted-foreground)); font-size:12px; }
.subtitle-path { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:520px; }
.subtitle-hero-card .n-card__content { padding:18px 20px; }
.subtitle-hero { display:flex; gap:14px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
.subtitle-hero-left { display:flex; gap:14px; align-items:center; }
.subtitle-hero-icon { width:44px; height:44px; border-radius:12px; display:flex; align-items:center; justify-content:center; color:hsl(var(--primary)); background:hsl(var(--primary) / 0.12); flex:0 0 auto; }
.subtitle-hero h2 { margin:0; font-size:20px; color:hsl(var(--foreground)); }
.subtitle-hero p { margin:4px 0 0; font-size:13px; color:hsl(var(--muted-foreground)); }
.subtitle-hero-actions { display:flex; gap:8px; align-items:center; }
@media (max-width: 900px) { .subtitle-grid { grid-template-columns:1fr; } }
`;

export function setPluginHost(injectedHost) {
  host = injectedHost;
}

export function getPluginHost() {
  return host;
}

export function ensureStyles() {
  if (typeof document === 'undefined') return;
  if (document.getElementById('subtitle-plugin-style')) return;
  const style = document.createElement('style');
  style.id = 'subtitle-plugin-style';
  style.textContent = CSS;
  document.head.appendChild(style);
}

export async function apiPost(path, body) {
  return host.api.post(`${API_BASE}/${path}`, body);
}

export async function apiGet(path, params) {
  return host.api.get(`${API_BASE}/${path}`, { params });
}

export function pageTitle(h, icon, title, description, actions = []) {
  const { NCard } = host.NaiveUI;
  return h(NCard, { bordered: false, class: 'subtitle-hero-card' }, [
    h('div', { class: 'subtitle-hero' }, [
      h('div', { class: 'subtitle-hero-left' }, [
        h('div', { class: 'subtitle-hero-icon' }, [h(host.IconifyIcon, { icon, class: 'size-5' })]),
        h('div', {}, [
          h('h2', {}, title),
          h('p', {}, description),
        ]),
      ]),
      actions.length ? h('div', { class: 'subtitle-hero-actions' }, actions) : null,
    ]),
  ]);
}

export const JOB_STATUS_LABELS = {
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失败',
  interrupted: '已中断',
};

export const ITEM_STATUS_LABELS = {
  done: '完成',
  skipped: '跳过',
  failed: '失败',
};

export const JOB_ACTION_LABELS = {
  exists: '已存在',
  would_download: '将下载',
  downloaded: '已下载',
  skipped: '跳过',
  failed: '失败',
};

export const PLACE_ACTION_LABELS = {
  placed: '已落盘',
  overwritten: '已覆盖',
  skipped: '已存在',
  failed: '失败',
  dry_run: '预览',
  needs_confirmation: '需确认',
};

export function jobStatusType(status) {
  if (status === 'failed') return 'error';
  if (status === 'completed') return 'success';
  if (status === 'running' || status === 'queued') return 'info';
  if (status === 'interrupted') return 'warning';
  return 'default';
}

export function placeActionType(action, success) {
  if (action === 'failed' || !success) return 'error';
  if (action === 'skipped') return 'default';
  return 'success';
}
