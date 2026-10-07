/**
 * 模块解析钩子：把裸导入 `@deepseek-ai/dsh-tools` 指向 DSH 运行时里的真实副本
 * （`<安装目录>\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-tools`），
 * 这样就能在不启动 DSH 的情况下加载插件宿主半部。入口由 test_host.mjs 传入。
 */
const entry = process.env.DSH_TOOLS_ENTRY;

export async function resolve(specifier, context, next) {
  if (entry && specifier === "@deepseek-ai/dsh-tools") {
    return { url: entry, shortCircuit: true };
  }
  return next(specifier, context);
}
