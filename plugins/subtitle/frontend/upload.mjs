/**
 * 字幕上传页：选择文件 → 匹配媒体 → 确认落盘.
 */

import { apiPost, ensureStyles, getPluginHost, pageTitle, placeActionType, PLACE_ACTION_LABELS } from './shared.mjs';

export function UploadPage() {
  const host = getPluginHost();
  const { defineComponent } = host.Vue;
  return defineComponent({
    name: 'SubtitleUploadPage',
    setup() {
      const { h, ref, computed } = host.Vue;
      const {
        NAlert,
        NButton,
        NCard,
        NDataTable,
        NEmpty,
        NForm,
        NFormItem,
        NInput,
        NInputNumber,
        NSelect,
        NSpace,
        NTag,
        NUpload,
      } = host.NaiveUI;

      ensureStyles();

      const uploadFiles = ref([]);
      const files = ref([]);
      const mode = ref('filename');
      const language = ref('zh-Hans');
      const flags = ref('');
      const targetDir = ref('');
      const targetVideo = ref('');
      const tmdbId = ref('');
      const mediaType = ref('movie');
      const season = ref(null);
      const episode = ref(null);
      const matching = ref(false);
      const uploading = ref(false);
      const matches = ref([]);
      const selected = ref({});
      const result = ref(null);
      const error = ref('');

      const modeOptions = [
        { label: '按字幕文件名', value: 'filename' },
        { label: '手动指定视频', value: 'target_video' },
        { label: '按 TMDB ID', value: 'tmdb' },
        { label: '目录唯一视频', value: 'directory' },
      ];
      const typeOptions = [
        { label: '电影', value: 'movie' },
        { label: '电视剧', value: 'tv' },
        { label: '动漫', value: 'anime' },
      ];

      const readyToUpload = computed(
        () =>
          files.value.length > 0 &&
          files.value.every((file) => selected.value[file.name]),
      );

      function resetResults() {
        matches.value = [];
        selected.value = {};
        result.value = null;
      }

      function onUploadFiles(list) {
        uploadFiles.value = list || [];
        files.value = uploadFiles.value.map((item) => item.file).filter(Boolean);
        resetResults();
      }

      function setMode(value) {
        mode.value = value;
        resetResults();
      }

      function validate() {
        if (!files.value.length) return '请先选择字幕文件';
        if (mode.value === 'target_video' && !targetVideo.value.trim()) return '请填写目标视频路径';
        if (mode.value === 'tmdb' && !tmdbId.value.trim()) return '请填写 TMDB ID';
        if (['filename', 'directory'].includes(mode.value) && !targetDir.value.trim()) return '请填写目标目录';
        return '';
      }

      function parseFlags() {
        return flags.value.split(',').map((item) => item.trim()).filter(Boolean);
      }

      async function match() {
        error.value = '';
        const invalid = validate();
        if (invalid) {
          error.value = invalid;
          return;
        }
        matching.value = true;
        try {
          const data = await apiPost('match', {
            backend_id: 'local',
            mode: mode.value,
            target_dir: targetDir.value.trim() || undefined,
            target_video: targetVideo.value.trim() || undefined,
            tmdb_id: tmdbId.value.trim() || undefined,
            type: mediaType.value,
            season: season.value,
            episode: episode.value,
            files: files.value.map((file) => ({
              filename: file.name,
              size: file.size,
              language: language.value,
              flags: parseFlags(),
            })),
          });
          matches.value = data || [];
          const next = {};
          for (const item of matches.value) {
            if (item.selected) next[item.filename] = item.selected.video_path;
          }
          selected.value = next;
        } catch (e) {
          error.value = e?.message || String(e);
        } finally {
          matching.value = false;
        }
      }

      async function upload() {
        error.value = '';
        uploading.value = true;
        try {
          const mappings = [];
          for (const file of files.value) {
            const videoPath = selected.value[file.name];
            if (!videoPath) continue;
            mappings.push({
              subtitle_filename: file.name,
              video_path: videoPath,
              backend_id: 'local',
              language: language.value,
              flags: parseFlags(),
              sync: { offset_ms: 0, rate: 1, method: 'manual', confidence: 0 },
            });
          }
          if (!mappings.length) throw new Error('请先为字幕选择目标视频');
          const payload = [];
          for (const file of files.value) {
            payload.push({
              filename: file.name,
              content: await fileToBase64(file),
              language: language.value,
            });
          }
          result.value = await apiPost('upload', {
            files: payload,
            mappings,
            dry_run: false,
          });
        } catch (e) {
          error.value = e?.message || String(e);
        } finally {
          uploading.value = false;
        }
      }

      const columns = computed(() => [
        { title: '字幕', key: 'filename', ellipsis: { tooltip: true } },
        {
          title: '语言',
          key: 'language',
          width: 140,
          render: (row) =>
            `${row.language || ''}${row.flags?.length ? ` / ${row.flags.join(',')}` : ''}`,
        },
        {
          title: '目标视频',
          key: 'video',
          render: (row) =>
            h(NSelect, {
              size: 'small',
              value: selected.value[row.filename] || null,
              placeholder: row.candidates?.length ? '请选择候选' : '无候选',
              options: (row.candidates || []).map((candidate) => ({
                label: `${candidate.video_path}（${candidate.confidence}%）`,
                value: candidate.video_path,
              })),
              'onUpdate:value': (value) => {
                selected.value = { ...selected.value, [row.filename]: value };
                result.value = null;
              },
            }),
        },
      ]);

      const resultColumns = [
        { title: '目标字幕', key: 'target_path', ellipsis: { tooltip: true } },
        {
          title: '结果',
          key: 'action',
          width: 120,
          render: (row) =>
            h(
              NTag,
              { size: 'small', type: placeActionType(row.action, row.success) },
              { default: () => PLACE_ACTION_LABELS[row.action] || row.action },
            ),
        },
        { title: '原因', key: 'reason', width: 180 },
      ];

      return () =>
        h('div', { class: 'subtitle-plugin' }, [
          pageTitle(h, 'lucide:subtitles', '字幕上传', '上传字幕后匹配视频，支持多语言、多字幕与识别重命名。'),
          h('div', { class: 'subtitle-grid' }, [
            h(NCard, { title: '上传与目标', bordered: false, size: 'small' }, [
              h(NForm, { labelPlacement: 'top', size: 'small' }, [
                h(NFormItem, { label: '字幕文件' }, [
                  h(NUpload, {
                    multiple: true,
                    defaultUpload: false,
                    accept: '.srt,.ass,.ssa,.vtt,.sub,.idx,.sup',
                    fileList: uploadFiles.value,
                    'onUpdate:fileList': onUploadFiles,
                  }),
                ]),
                h(NFormItem, { label: '匹配方式' }, [
                  h(NSelect, {
                    value: mode.value,
                    options: modeOptions,
                    'onUpdate:value': setMode,
                  }),
                ]),
                h(NFormItem, { label: '目标目录' }, [
                  h(NInput, {
                    value: targetDir.value,
                    placeholder: '/media/movies',
                    'onUpdate:value': (value) => {
                      targetDir.value = value;
                      resetResults();
                    },
                  }),
                ]),
                mode.value === 'target_video'
                  ? h(NFormItem, { label: '目标视频' }, [
                      h(NInput, {
                        value: targetVideo.value,
                        placeholder: '/media/movies/Movie.mkv',
                        'onUpdate:value': (value) => {
                          targetVideo.value = value;
                          resetResults();
                        },
                      }),
                    ])
                  : null,
                mode.value === 'tmdb'
                  ? h(NFormItem, { label: 'TMDB ID' }, [
                      h(NInput, {
                        value: tmdbId.value,
                        placeholder: 'TMDB ID',
                        'onUpdate:value': (value) => {
                          tmdbId.value = value;
                          resetResults();
                        },
                      }),
                    ])
                  : null,
                h(NFormItem, { label: '媒体类型' }, [
                  h(NSelect, {
                    value: mediaType.value,
                    options: typeOptions,
                    'onUpdate:value': (value) => {
                      mediaType.value = value;
                      resetResults();
                    },
                  }),
                ]),
                ['tv', 'anime'].includes(mediaType.value)
                  ? h(NSpace, { size: 8 }, [
                      h(NInputNumber, {
                        value: season.value,
                        placeholder: '季',
                        min: 0,
                        style: 'width:120px',
                        'onUpdate:value': (value) => {
                          season.value = value;
                          resetResults();
                        },
                      }),
                      h(NInputNumber, {
                        value: episode.value,
                        placeholder: '集',
                        min: 0,
                        style: 'width:120px',
                        'onUpdate:value': (value) => {
                          episode.value = value;
                          resetResults();
                        },
                      }),
                    ])
                  : null,
                h(NSpace, { size: 8 }, [
                  h(NInput, {
                    value: language.value,
                    placeholder: '语言，如 zh-Hans',
                    'onUpdate:value': (value) => {
                      language.value = value;
                      resetResults();
                    },
                  }),
                  h(NInput, {
                    value: flags.value,
                    placeholder: '标记，如 forced,sdh',
                    'onUpdate:value': (value) => {
                      flags.value = value;
                      resetResults();
                    },
                  }),
                ]),
                error.value
                  ? h(NAlert, { type: 'error', bordered: false, showIcon: false }, { default: () => error.value })
                  : null,
                h(NSpace, { size: 8 }, [
                  h(NButton, { type: 'primary', size: 'small', loading: matching.value, onClick: match }, { default: () => '匹配媒体' }),
                  h(
                    NButton,
                    {
                      size: 'small',
                      loading: uploading.value,
                      disabled: !readyToUpload.value,
                      onClick: upload,
                    },
                    { default: () => '上传并落盘' },
                  ),
                ]),
              ]),
            ]),
            h(NCard, { title: '匹配结果', bordered: false, size: 'small' }, [
              matches.value.length
                ? h(NDataTable, {
                    columns: columns.value,
                    data: matches.value,
                    size: 'small',
                    bordered: false,
                    pagination: false,
                    maxHeight: 420,
                    scrollX: 780,
                    rowKey: (row) => row.filename,
                  })
                : h(NEmpty, { description: '先选择字幕文件并点击「匹配媒体」' }),
              result.value?.length
                ? h(NDataTable, {
                    columns: resultColumns,
                    data: result.value,
                    size: 'small',
                    bordered: false,
                    pagination: false,
                    style: 'margin-top:12px',
                  })
                : null,
            ]),
          ]),
        ]);
    },
  });
}

async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}
