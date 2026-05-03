const titlebarMenus = [
  { key: 'file', label: '文件' },
  { key: 'edit', label: '编辑' },
  { key: 'view', label: '查看' },
  { key: 'window', label: '窗口' },
  { key: 'help', label: '帮助' },
];

export function ElectronTitlebar() {
  if (!window.funharness?.isElectron) {
    return null;
  }

  return (
    <div className="electron-titlebar">
      <div className="electron-titlebar-menus">
        {titlebarMenus.map((item) => (
          <button key={item.key} type="button" onClick={() => void window.funharness?.showMenu?.(item.key)}>
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}
