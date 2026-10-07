/**
 * 常见的模型服务。地址都是 OpenAI 兼容接口；模型名只是举例，以“读取模型列表”拿到的为准，
 * 模型更新换代后不需要改这里。
 */
export interface ModelProvider {
  key: string
  label: string
  baseUrl: string
  /** 是否需要密钥（本机的 Ollama、LM Studio 不需要） */
  needsKey: boolean
  examples: string[]
  note?: string
}

export const LOCAL_PROVIDERS: ModelProvider[] = [
  { key: 'ollama', label: 'Ollama（笔记本推荐）', baseUrl: 'http://127.0.0.1:11434/v1', needsKey: false, examples: ['qwen3:4b', 'qwen3:8b', 'qwen3:14b'],
    note: '安装 Ollama 后，在命令行执行 ollama pull 加模型名下载模型。' },
  { key: 'lmstudio', label: 'LM Studio', baseUrl: 'http://127.0.0.1:1234/v1', needsKey: false, examples: [],
    note: '在 LM Studio 里加载模型，并在“开发者”页面开启本地服务。' },
  { key: 'vllm', label: 'vLLM（GPU 服务器）', baseUrl: 'http://127.0.0.1:8001/v1', needsKey: true, examples: ['Qwen/Qwen3-14B-AWQ', 'Qwen/Qwen3-8B'],
    note: '用 docker compose --profile gpu 启动时，密钥是 .env 里的 PRIVSHIELD_LLM_API_KEY。服务在别的机器上时，把地址里的 127.0.0.1 换成服务器 IP。' },
  { key: 'custom', label: '其他兼容接口', baseUrl: '', needsKey: true, examples: [] },
]

export const CLOUD_PROVIDERS: ModelProvider[] = [
  { key: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', needsKey: true, examples: ['deepseek-chat'] },
  { key: 'dashscope', label: '阿里云百炼（通义千问）', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', needsKey: true, examples: ['qwen-plus', 'qwen-turbo', 'qwen-max'] },
  { key: 'moonshot', label: '月之暗面 Kimi', baseUrl: 'https://api.moonshot.cn/v1', needsKey: true, examples: ['moonshot-v1-8k'] },
  { key: 'zhipu', label: '智谱 GLM', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', needsKey: true, examples: ['glm-4-flash', 'glm-4-plus'] },
  { key: 'siliconflow', label: '硅基流动', baseUrl: 'https://api.siliconflow.cn/v1', needsKey: true, examples: ['Qwen/Qwen3-8B', 'deepseek-ai/DeepSeek-V3'] },
  { key: 'ark', label: '火山方舟（豆包）', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', needsKey: true, examples: [],
    note: '模型名称填方舟控制台里的模型 ID 或推理接入点 ID。' },
  { key: 'openai', label: 'OpenAI', baseUrl: 'https://api.openai.com/v1', needsKey: true, examples: ['gpt-4o-mini'] },
  { key: 'custom', label: '其他兼容接口', baseUrl: '', needsKey: true, examples: [] },
]

/** 按地址认出是哪家服务（.env 里配置的、或旧版本保存的设置没有记录服务商）。 */
export function providerFor(list: ModelProvider[], key: string, baseUrl: string): ModelProvider {
  const byKey = list.find(item => item.key === key)
  if (byKey) return byKey
  const host = (() => { try { return new URL(baseUrl).host } catch { return '' } })()
  return list.find(item => item.baseUrl && new URL(item.baseUrl).host === host) || list[list.length - 1]
}

/** 本地模型按电脑配置的大致选择（量化版本，Ollama 默认下载的就是）。 */
export const HARDWARE_GUIDE: Array<[string, string]> = [
  ['8 GB 内存，没有独立显卡', '3B 到 4B 的模型，例如 qwen3:4b'],
  ['16 GB 内存，或 6 到 8 GB 显存', '7B 到 8B，例如 qwen3:8b'],
  ['16 GB 以上显存', '14B 左右，例如 qwen3:14b'],
  ['24 GB 以上显存或 GPU 服务器', '30B 以上，或用 vLLM 部署'],
]
