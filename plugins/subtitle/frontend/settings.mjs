/** 设置页兜底组件；真正的设置由 manifest fields 自动渲染。 */

import { ensureStyles, getPluginHost } from './shared.mjs';

export function SettingsPage() {
  const host = getPluginHost();
  const { defineComponent } = host.Vue;
  return defineComponent({
    name: 'SubtitleSettingsPage',
    setup() {
      const { h } = host.Vue;
      const { NAlert, NCard } = host.NaiveUI;
      ensureStyles();
      return () =>
        h('div', { class: 'subtitle-plugin' }, [
          h(NCard, { title: '字幕管理设置', bordered: false, size: 'small' }, [
            h(NAlert, { type: 'info', bordered: false, showIcon: false }, {
              default: () => '请在插件设置中配置字幕源、语言、定时任务与时间轴策略。',
            }),
          ]),
        ]);
    },
  });
}
