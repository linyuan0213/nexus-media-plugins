/** 插件 ESM 入口：装配三个页面组件。 */

import { setPluginHost } from './shared.mjs';
import { LibraryPage } from './library.mjs';
import { RenamePage } from './rename.mjs';
import { SettingsPage } from './settings.mjs';
import { UploadPage } from './upload.mjs';

export default function (host) {
  setPluginHost(host);
  return {
    UploadPage: UploadPage(),
    RenamePage: RenamePage(),
    LibraryPage: LibraryPage(),
    SettingsPage: SettingsPage(),
  };
}
