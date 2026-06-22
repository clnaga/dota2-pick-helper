# Dota 2 Counter Helper

Dota 2 实时克制推荐工具，基于 STRATZ 数据。

## 环境准备

需要 Python 3.10+，推荐用 [uv](https://docs.astral.sh/uv/) 管理环境：

```bash
# 安装 uv（如果没有）
pip install uv

# 创建虚拟环境并安装依赖
uv venv
.venv\Scripts\activate   # Windows
uv pip install -r requirements.txt
```

## 启动

```bash
python server.py
```

打开浏览器访问 `http://127.0.0.1:3002`。

- 端口 3001：GSI 接收器（接收 Dota 2 游戏状态）
- 端口 3002：Web UI

游戏内选人阶段会自动显示克制推荐。也可以点击 LIVE 按钮切换到 TEST 模式手动添加英雄测试。

## 同步数据

数据已预置在 `data/matchups.json`，开箱即用。如需更新：

1. 在 [STRATZ](https://stratz.com/api) 免费申请 Token
2. 复制 `.env.example` 为 `.env`，填入 Token
3. 执行：

```bash
python sync_data.py all       # 拉取英雄 + 克制数据
python sync_data.py heroes    # 仅英雄
python sync_data.py counters  # 仅克制
```

## data 目录

| 文件 | 说明 |
|------|------|
| `heroes.json` | 英雄列表（含中文名、属性、定位） |
| `matchups.json` | 127 英雄克制 + 搭配数据 |
| `docs.json` | STRATZ GraphQL Schema 文档（给 AI 读的） |
| `chinese_names.txt` | 中文名映射表 |

## 推荐算法

### 数据来源

`matchups.json` 中每个英雄对包含两个 STRATZ 指标：

| 字段 | 含义 |
|------|------|
| `vs[].synergy` | 克制强度（正=克制对方，负=被对方克制） |
| `with[].synergy` | 配合强度（正=适合做队友） |
| `winsAverage` | 真实对阵/组队胜率 |

### 三栏计算规则

**推荐选择（Picks）**— 针对敌方阵容选克制英雄：

```
score = Σ advantage（对每个敌人）
其中 advantage = |synergy|（STRATZ 克制值）
敌人是辅助 → advantage × 0.4（针对辅助选人价值低）
```

**建议禁用（Bans）**— 针对我方阵容 ban 掉威胁英雄：

```
score = Σ advantage（克制每个队友的威胁值）
队友是辅助 → advantage × 0.4（保护辅助价值低）
```

**推荐搭配（Allies）**— 针对我方已选英雄找最佳队友：

```
score = Σ with[].synergy（对每个队友的配合值）
无辅助权重折扣
```

按 score 降序排列取 Top 10，winRate 为各场对阵胜率的算术平均仅供参考。
