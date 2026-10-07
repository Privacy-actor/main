import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, RefreshCw } from 'lucide-react'
import { BRAND } from '../brand'
import { Kbd, Spinner } from '../components/ui'
import { describeEngines, useApp } from '../hooks/AppContext'
import { EPSILON } from '../lib/entities'
import './Guide.css'

export const GUIDE_SEEN_KEY = 'privshield.guideSeen'

type Dot = 'on' | 'lite' | 'idle' | 'warn' | 'off'

function Check({ state, title, status, children, action }: { state: Dot; title: string; status: ReactNode; children: ReactNode; action?: ReactNode }) {
  return <li className="guide-check">
    <i className={`engine-dot is-${state}`} aria-hidden="true"/>
    <div className="guide-check-main">
      <div className="guide-check-head"><strong>{title}</strong><span className="guide-check-status">{status}</span></div>
      <p>{children}</p>
    </div>
    {action && <div className="guide-check-action">{action}</div>}
  </li>
}

const shortName = (name: string) => name.split(/[\\/]/).filter(Boolean).pop() || name

/** 上手指南：准备清单（实时状态）、用示例走一遍完整流程、三种方式怎么选、常见问题。第一次打开时自动进入。 */
export default function Guide() {
  const { engine, refreshEngine, projects, currentProject } = useApp()
  const engines = describeEngines(engine.models)
  const offline = engine.state === 'offline'
  const checking = engine.state === 'checking'
  const local = engine.models?.endpoints?.local
  const cloud = engine.models?.endpoints?.cloud

  useEffect(() => {
    try { localStorage.setItem(GUIDE_SEEN_KEY, '1') } catch { /* 隐私模式下写不进去也不影响使用 */ }
  }, [])

  const llmStatus = [local?.enabled && `本地 ${shortName(local.model)}`, cloud?.enabled && `云端 ${shortName(cloud.model)}`].filter(Boolean).join('，')

  return <div className="page guide">
    <header className="page-head">
      <div>
        <h1 className="page-title">上手指南</h1>
        <p className="page-desc">第一次使用{BRAND.name}，先看下面的准备情况，再用示例完整处理一段文本，大约五分钟。以后可以随时从侧栏回到这一页。</p>
      </div>
      <div className="page-actions"><Link className="btn btn-primary" to="/workbench?sample=interview">用示例开始<ArrowRight size={15}/></Link></div>
    </header>

    <section className="section">
      <div className="section-head">
        <h2 className="section-title">准备情况</h2>
        <button type="button" className="btn btn-sm btn-quiet" onClick={() => void refreshEngine()} disabled={checking}>{checking ? <Spinner size={13}/> : <RefreshCw size={14}/>}重新检测</button>
      </div>
      <ul className="guide-checks panel">
        <Check state={offline ? 'off' : checking ? 'idle' : 'on'} title="处理服务"
          status={checking ? '检测中' : offline ? '未连接' : `已连接，版本 ${engine.health?.version || ''}`}>
          {offline
            ? <>在 Windows 上双击项目根目录的 <code>启动墨隐.cmd</code>，等后端和前端两个窗口都启动后点“重新检测”。命令行启动方法见项目的 README。</>
            : '识别、脱敏和任务记录都由它完成，原文保存在本机的数据库里。'}
        </Check>
        <Check state={offline ? 'off' : engines.nerModel ? 'on' : 'lite'} title="规则与 NER 识别"
          status={offline ? '—' : engines.nerModel ? `NER 模型 ${engines.ner}` : '内置规则和轻量识别器'}
          action={!offline && !engines.nerModel && <Link className="btn btn-sm" to="/system#ner">安装 NER 模型</Link>}>
          手机号、证件号、银行卡等号码由规则层识别；姓名、机构、地点由 NER 层识别。{engines.nerModel ? '多语种 NER 模型已加载。' : '可选安装多语种 NER 模型，提高姓名、机构和地点的识别率；不装也能用。'}
        </Check>
        <Check state={offline ? 'off' : engines.localLlm ? 'on' : engines.cloudLlm ? 'lite' : 'idle'} title="大模型核查（可选）"
          status={offline ? '—' : llmStatus || '未启用'}
          action={!offline && <Link className="btn btn-sm" to="/system#models">{engines.llmReady ? '更换模型' : '选择模型'}</Link>}>
          复核置信度不足的实体、补充前两层漏掉的信息，并理解用一句话写的要求。电脑跑不动本地模型时可以用云端接口，只发送需要核查的句子。不接大模型时，这部分交给人工确认。
          {!offline && engines.cloudLlm && !engines.localLlm && <> 只设置了云端模型：处理方案“更多设置”里的部署模式选“云端”后才会使用。</>}
        </Check>
        <Check state="idle" title="浏览器插件（可选）" status="在浏览器里安装"
          action={<Link className="btn btn-sm" to="/system#plugin">安装步骤</Link>}>
          在 ChatGPT、豆包、Kimi 等对话框里选中文字，按 <Kbd>Alt</Kbd> <Kbd>Shift</Kbd> <Kbd>M</Kbd> 就地换成脱敏结果再发送。
        </Check>
        <Check state={projects.length ? 'on' : 'idle'} title="项目（可选）"
          status={projects.length ? `${projects.length} 个项目${currentProject ? `，当前「${currentProject.name}」` : ''}` : '还没有项目'}
          action={<Link className="btn btn-sm" to={projects.length ? '/projects' : '/projects?new=1'}>{projects.length ? '管理项目' : '新建项目'}</Link>}>
          反复处理同一类材料（访谈、客服工单、病历）时，把脱敏方式、识别范围和专属规则存成项目，下次在侧栏切换即可。
        </Check>
      </ul>
    </section>

    <section className="section">
      <div className="section-head"><h2 className="section-title">用示例走一遍</h2></div>
      <ol className="guide-steps">
        <li>
          <div className="guide-step-copy">
            <h3>放入文本，点“识别并脱敏”</h3>
            <p>在工作台粘贴文字，或导入 TXT、Word、PDF、CSV 等文件。选好脱敏方式和力度，也可以用一句话写要求，比如“保留北京的地名，姓名用差分隐私替换”，先点“看看会怎么理解”确认。</p>
          </div>
          <Link className="btn btn-sm" to="/workbench?sample=interview">打开示例</Link>
        </li>
        <li>
          <div className="guide-step-copy">
            <h3>逐条复核</h3>
            <p>右侧列表先看标为“待确认”的实体：<Kbd>A</Kbd> 确认脱敏，<Kbd>R</Kbd> 恢复原文，<Kbd>J</Kbd> <Kbd>K</Kbd> 上下切换。同一个名字可以一起处理；识别错了可以改类型、调整范围；漏掉的在原文里选中后补上。</p>
          </div>
        </li>
        <li>
          <div className="guide-step-copy">
            <h3>修改最终稿</h3>
            <p>切到“最终稿”可以直接改字，系统自动保存版本，可以撤销、查找替换，也能和自动结果对比。</p>
          </div>
        </li>
        <li>
          <div className="guide-step-copy">
            <h3>导出</h3>
            <p>导出 TXT、Word、处理报告或审计记录。导出前会自动复检一遍，发现残留的号码、姓名或职务身份会先提醒，并能定位到最终稿里的位置。</p>
          </div>
        </li>
        <li>
          <div className="guide-step-copy">
            <h3>处理更多文件</h3>
            <p>多个文件用“批量处理”：先拿几段试跑看效果，再处理全部，导出时 CSV、JSON 还原成原来的列。所有任务里待确认的实体集中在“人工复核”。</p>
          </div>
          <Link className="btn btn-sm" to="/batch">批量处理</Link>
        </li>
      </ol>
    </section>

    <section className="section">
      <div className="section-head"><h2 className="section-title">三种脱敏方式怎么选</h2></div>
      <div className="table-wrap">
        <table className="table guide-table">
          <thead><tr><th>方式</th><th>效果</th><th>适合</th><th>力度的作用</th></tr></thead>
          <tbody>
            <tr>
              <td data-label="方式"><strong>掩码</strong></td>
              <td data-label="效果"><span className="guide-from">张伟</span><span className="guide-arrow">→</span><span className="guide-mask">PERSON-001</span></td>
              <td data-label="适合">要彻底去掉身份信息，又想知道“同一个人出现了几次”。交给大模型分析、对外提供数据时常用。</td>
              <td data-label="力度的作用">不分力度。同一实体全文编号一致。</td>
            </tr>
            <tr>
              <td data-label="方式"><strong>差分隐私替换</strong></td>
              <td data-label="效果"><span className="guide-from">张伟</span><span className="guide-arrow">→</span><span className="guide-swap">林清禾</span></td>
              <td data-label="适合">访谈、文章等需要读起来自然的材料。大学换成大学、医院换成医院。</td>
              <td data-label="力度的作用">力度越强，替换词越随机（ε 依次为 {EPSILON[1]}、{EPSILON[2]}、{EPSILON[3]}）。</td>
            </tr>
            <tr>
              <td data-label="方式"><strong>知识图谱泛化</strong></td>
              <td data-label="效果"><span className="guide-from">北京协和医院</span><span className="guide-arrow">→</span><span className="guide-swap">北京某医院</span></td>
              <td data-label="适合">需要保留类别和地域做统计分析，例如“哪类机构、哪个城市”。</td>
              <td data-label="力度的作用">力度越强，上溯得越笼统：北京某医院、医疗机构。</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="guide-note">也可以在“规则库”里按实体类型分别设置方式，比如姓名用差分隐私替换、号码一律掩码。</p>
    </section>

    <section className="section">
      <div className="section-head"><h2 className="section-title">常见问题</h2></div>
      <dl className="guide-faq panel panel-pad">
        <div>
          <dt>为什么有的实体标成“待确认”？</dt>
          <dd>识别置信度低于 90%，或几个识别层判断不一致。默认先按脱敏处理，复核时可以恢复原文；也可以在“更多设置”里改为先保留原文、确认后再替换。</dd>
        </div>
        <div>
          <dt>不接大模型能用吗？</dt>
          <dd>能。规则层和 NER 层照常识别，置信度不足的实体交给你确认。接上大模型后，这些实体会先由模型复核，人工要看的会少很多。</dd>
        </div>
        <div>
          <dt>用云端模型时，云端会看到什么？</dt>
          <dd>只发送需要核查的句子，不发送全文。涉密材料请用本地部署，或者关闭大模型核查。</dd>
        </div>
        <div>
          <dt>识别错了怎么办？</dt>
          <dd>在工作台选中这个实体：不是隐私就恢复原文，类型不对就改类型，范围不对就调整范围，漏掉的在原文里选中后补上。常见的编号格式可以在规则库里加成规则，以后自动识别。</dd>
        </div>
        <div>
          <dt>数据存在哪里，怎么删？</dt>
          <dd>原文、结果和最终稿保存在本机的数据库里。在“任务记录”里可以删除单个任务，或按天数清理旧任务。操作记录只保存位置、长度和哈希，不含明文。</dd>
        </div>
        <div>
          <dt>浏览器插件没反应？</dt>
          <dd>先确认后端在运行，再打开插件的设置页点“测试连接”：服务地址应形如 <code>http://127.0.0.1:8000/api/v1</code>。浏览器自己的设置页、扩展商店等页面不允许插件读取，会在插件图标上显示感叹号。</dd>
        </div>
      </dl>
    </section>
  </div>
}
