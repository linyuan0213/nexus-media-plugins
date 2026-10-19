/**
 * 全量补字幕页：扫描媒体库，缺失中文字幕时按语言下载.
 */

import { apiGet, apiPost, ensureStyles, getPluginHost, ITEM_STATUS_LABELS, JOB_ACTION_LABELS, JOB_STATUS_LABELS, jobStatusType, pageTitle } from './shared.mjs';

export function LibraryPage() {
  const host = getPluginHost();
  const { defineComponent } = host.Vue;
  return defineComponent({
    name: 'SubtitleLibraryPage',
    setup() {
      const { h, ref, computed, onBeforeUnmount, onMounted } = host.Vue;
      const {
        NAlert,
        NButton,
        NCard,
        NDataTable,
        NDynamicTags,
        NEmpty,
        NProgress,
        NSelect,
        NSpace,
        NSwitch,
        NTag,
      } = host.NaiveUI;

      ensureStyles();

      const roots = ref([]);
      const mediaTypes = ref(['movie', 'tv', 'anime']);
      const dryRun = ref(false);
      const loading = ref(false);
      const job = ref(null);
      const error = ref('');
      let timer = null;
      const STORAGE_KEY = 'subtitle.library.job_id';

      function remember(id) {
        try {
          if (id) localStorage.setItem(STORAGE_KEY, id);
          else localStorage.removeItem(STORAGE_KEY);
        } catch {
          /* 隐私模式下 localStorage 不可用时忽略 */
        }
      }

      const mediaTypeOptions = [
        { label: '电影', value: 'movie' },
        { label: '电视剧', value: 'tv' },
        { label: '动漫', value: 'anime' },
      ];

      function stop() {
        if (timer) {
          clearTimeout(timer);
          timer = null;
        }
      }

      async function poll(id) {
        try {
          job.value = await apiGet('library/job', { job_id: id });
          if (['completed', 'cancelled', 'failed', 'interrupted'].includes(job.value?.status)) {
            stop();
          } else {
            timer = setTimeout(() => poll(id), 2000);
          }
        } catch (e) {
          if (job.value?.id === id) {
            remember(null);
            job.value = null;
          }
          error.value = e?.message || String(e);
          stop();
        }
      }

      async function start() {
        error.value = '';
        loading.value = true;
        stop();
        try {
          const result = await apiPost('library/scan', {
            roots: roots.value,
            media_types: mediaTypes.value,
            dry_run: dryRun.value,
          });
          job.value = result;
          if (result?.id) {
            remember(result.id);
            timer = setTimeout(() => poll(result.id), 1000);
          }
        } catch (e) {
          error.value = e?.message || String(e);
        } finally {
          loading.value = false;
        }
      }

      async function cancel() {
        if (!job.value?.id) return;
        try {
          job.value = await apiPost('library/cancel', { job_id: job.value.id });
          stop();
        } catch (e) {
          error.value = e?.message || String(e);
        }
      }

      onMounted(() => {
        let saved = null;
        try {
          saved = localStorage.getItem(STORAGE_KEY);
        } catch {
          /* ignore */
        }
        if (saved) {
          job.value = { id: saved };
          poll(saved);
        }
      });

      onBeforeUnmount(stop);

      const processed = computed(() =>
        job.value ? job.value.done + job.value.skipped + job.value.failed : 0,
      );
      const percent = computed(() =>
        job.value?.total ? Math.round((processed.value / job.value.total) * 100) : 0,
      );
      const running = computed(
        () => job.value?.status === 'queued' || job.value?.status === 'running',
      );

      const columns = [
        { title: '视频', key: 'video_path', ellipsis: { tooltip: true } },
        {
          title: '状态',
          key: 'status',
          width: 110,
          render: (row) =>
            h(
              NTag,
              { size: 'small', type: row.status === 'failed' ? 'error' : row.status === 'done' ? 'success' : 'default' },
              { default: () => ITEM_STATUS_LABELS[row.status] || row.status || '—' },
            ),
        },
        {
          title: '动作',
          key: 'action',
          width: 130,
          render: (row) => JOB_ACTION_LABELS[row.action] || row.action || '—',
        },
        { title: '原因', key: 'reason', width: 180 },
      ];

      return () =>
        h('div', { class: 'subtitle-plugin' }, [
          pageTitle(h, 'lucide:library-big', '全量补字幕', '扫描媒体库，已有中文字幕跳过；缺失时按语言自动下载。'),
          h(NCard, { title: '扫描范围', bordered: false, size: 'small' }, [
            h(NSpace, { vertical: true, size: 10 }, [
              h(NDynamicTags, {
                value: roots.value,
                placeholder: '留空使用已配置媒体库，例如 /media/movies',
                'onUpdate:value': (value) => {
                  roots.value = value;
                },
              }),
              h(NSpace, { size: 12, align: 'center' }, [
                h(NSelect, {
                  value: mediaTypes.value,
                  options: mediaTypeOptions,
                  multiple: true,
                  'onUpdate:value': (value) => {
                    mediaTypes.value = value;
                  },
                }),
                h('span', { class: 'subtitle-muted' }, '只预检'),
                h(NSwitch, {
                  value: dryRun.value,
                  'onUpdate:value': (value) => {
                    dryRun.value = value;
                  },
                }),
                h(NButton, {
                  type: 'primary',
                  size: 'small',
                  loading: loading.value,
                  disabled: running.value,
                  onClick: start,
                }, { default: () => '开始扫描' }),
                running.value
                  ? h(NButton, { size: 'small', onClick: cancel }, { default: () => '取消' })
                  : null,
              ]),
              error.value
                ? h(NAlert, { type: 'error', bordered: false, showIcon: false }, { default: () => error.value })
                : null,
            ]),
          ]),
          job.value
            ? h(NCard, { title: `任务 ${job.value.id?.slice(0, 8) || ''}`, bordered: false, size: 'small' }, [
                h(NSpace, { vertical: true, size: 10 }, [
                  h(NSpace, { size: 8, align: 'center' }, [
                    h(NTag, { size: 'small', type: jobStatusType(job.value.status) }, { default: () => JOB_STATUS_LABELS[job.value.status] || job.value.status }),
                    h('span', { class: 'subtitle-muted' }, `${processed.value} / ${job.value.total} · 完成 ${job.value.done} · 跳过 ${job.value.skipped} · 失败 ${job.value.failed}`),
                  ]),
                  h(NProgress, { percentage: percent.value, showIndicator: false }),
                  job.value.current ? h('div', { class: 'subtitle-muted' }, `当前：${job.value.current}`) : null,
                  h(NDataTable, {
                    columns,
                    data: job.value.results || [],
                    size: 'small',
                    bordered: false,
                    pagination: false,
                    maxHeight: 420,
                    scrollX: 900,
                  }),
                ]),
              ])
            : h(NCard, { title: '任务', bordered: false, size: 'small' }, [h(NEmpty, { description: '点击「开始扫描」创建全量补字幕任务' })]),
        ]);
    },
  });
}
