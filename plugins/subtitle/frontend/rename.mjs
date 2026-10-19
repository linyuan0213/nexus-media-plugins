/**
 * 识别重命名页：扫描已有字幕 → 预览 → 应用.
 */

import { apiPost, ensureStyles, getPluginHost, pageTitle, placeActionType, PLACE_ACTION_LABELS } from './shared.mjs';

export function RenamePage() {
  const host = getPluginHost();
  const { defineComponent } = host.Vue;
  return defineComponent({
    name: 'SubtitleRenamePage',
    setup() {
      const { h, ref, computed } = host.Vue;
      const {
        NAlert,
        NButton,
        NCard,
        NDataTable,
        NEmpty,
        NInput,
        NModal,
        NSpace,
        NSwitch,
        NTag,
      } = host.NaiveUI;

      ensureStyles();

      const path = ref('');
      const recursive = ref(true);
      const loading = ref(false);
      const rows = ref([]);
      const error = ref('');
      const confirmShow = ref(false);
      const pickerShow = ref(false);

      const riskyCount = computed(
        () => rows.value.filter((row) => ['failed', 'needs_confirmation'].includes(row.action)).length,
      );

      async function scan() {
        error.value = '';
        rows.value = [];
        if (!path.value.trim()) {
          error.value = '请填写扫描目录';
          return;
        }
        loading.value = true;
        try {
          rows.value = await apiPost('rename', {
            mode: 'recognize',
            scope: { backend_id: 'local', path: path.value.trim(), recursive: recursive.value },
            dry_run: true,
          });
        } catch (e) {
          error.value = e?.message || String(e);
        } finally {
          loading.value = false;
        }
      }

      async function applyNow() {
        confirmShow.value = false;
        error.value = '';
        loading.value = true;
        try {
          rows.value = await apiPost('rename', {
            mode: 'recognize',
            scope: { backend_id: 'local', path: path.value.trim(), recursive: recursive.value },
            dry_run: false,
          });
        } catch (e) {
          error.value = e?.message || String(e);
        } finally {
          loading.value = false;
        }
      }

      const columns = [
        { title: '旧路径', key: 'old_path', ellipsis: { tooltip: true } },
        { title: '新路径', key: 'new_path', ellipsis: { tooltip: true } },
        {
          title: '动作',
          key: 'action',
          width: 140,
          render: (row) =>
            h(
              NTag,
              { size: 'small', type: placeActionType(row.action, true) },
              { default: () => PLACE_ACTION_LABELS[row.action] || row.action || '—' },
            ),
        },
        { title: '原因', key: 'reason', width: 180 },
      ];

      return () =>
        h('div', { class: 'subtitle-plugin' }, [
          pageTitle(h, 'lucide:file-pen-line', '识别重命名', '扫描已有字幕，识别语言/标记并匹配视频；先预览，再应用。'),
          h(NCard, { title: '扫描范围', bordered: false, size: 'small' }, [
            h(NSpace, { vertical: true, size: 10 }, [
              h(NSpace, { size: 8, align: 'center' }, [
                h(NInput, {
                  value: path.value,
                  placeholder: '/media/movies',
                  'onUpdate:value': (value) => {
                    path.value = value;
                    rows.value = [];
                  },
                }),
                h(NButton, { size: 'small', onClick: () => { pickerShow.value = true; } }, { default: () => '选择目录' }),
              ]),
              h(NSpace, { size: 12, align: 'center' }, [
                h('span', { class: 'subtitle-muted' }, '递归'),
                h(NSwitch, {
                  value: recursive.value,
                  'onUpdate:value': (value) => {
                    recursive.value = value;
                    rows.value = [];
                  },
                }),
                h(NButton, { type: 'warning', size: 'small', loading: loading.value, onClick: scan }, { default: () => '预览重命名' }),
                h(NButton, {
                  type: 'error',
                  size: 'small',
                  loading: loading.value,
                  disabled: !rows.value.length || riskyCount.value > 0,
                  onClick: () => {
                    if (!path.value.trim()) {
                      error.value = '请填写扫描目录';
                      return;
                    }
                    confirmShow.value = true;
                  },
                }, { default: () => '应用重命名' }),
              ]),
              error.value ? h(NAlert, { type: 'error', bordered: false, showIcon: false }, { default: () => error.value }) : null,
            ]),
          ]),
          h(NCard, { title: '结果', bordered: false, size: 'small' }, [
            riskyCount.value
              ? h(NAlert, { type: 'warning', bordered: false, showIcon: false }, { default: () => `有 ${riskyCount.value} 条需要确认或失败，请先处理再应用` })
              : null,
            rows.value.length
              ? h(NDataTable, { columns, data: rows.value, size: 'small', bordered: false, pagination: false, maxHeight: 480, scrollX: 900 })
              : h(NEmpty, { description: '尚无结果' }),
          ]),
          h(host.PathPickerModal, {
            show: pickerShow.value,
            initialPath: path.value,
            title: '选择扫描目录',
            'onUpdate:show': (value) => { pickerShow.value = value; },
            onConfirm: (value) => {
              path.value = value;
              rows.value = [];
              pickerShow.value = false;
            },
          }),
          h(NModal, {
            show: confirmShow.value,
            preset: 'dialog',
            title: '应用识别重命名',
            type: 'warning',
            content: `将对 ${rows.value.length} 条字幕执行重命名，是否继续？`,
            positiveText: '应用',
            negativeText: '取消',
            loading: loading.value,
            'onPositiveClick': applyNow,
            'onNegativeClick': () => { confirmShow.value = false; },
            'onUpdate:show': (value) => { confirmShow.value = value; },
          }),
        ]);
    },
  });
}
