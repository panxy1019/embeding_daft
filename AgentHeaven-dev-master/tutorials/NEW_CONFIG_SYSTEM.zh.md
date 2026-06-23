# AgentHeaven 新配置系统适配

AgentHeaven使用一个通用的`ConfigManager`类来管理配置，这个通用的配置类并不局限于`ahvn`包，而是开发任何一个python包时都可以使用。例如`rubiksql`也引用这个类，通过创建一个新的实例来管理自己的配置。

## 1. 旧版配置系统

旧版配置系统基于文件，包含三层系统：本地配置 > 全局配置 > 默认配置。

- 本地配置：`./.ahvn/config.yaml`。
- 全局配置：`~/.ahvn/config.yaml`。
- 默认配置：`<ahvn的python包路径>/resources/default_config.yaml`。

安装时使用`ahvn setup [--reset/-r]`，会复制默认配置为全局配置。用户可以通过`ahvn config --global/-g`修改全局配置，或在当前目录使用`ahvn config --local/-l`创建/修改本地配置。

因此，在`rubiksql`包中需要使用`ahvn`的配置时，取巧强制将当前路径修改为`~/.rubiksql`，这就使得：

- RubikSQL 本地配置：`./.rubiksql/config.yaml`（几乎从不使用）。
- RubikSQL 全局配置：`~/.rubiksql/config.yaml`（实际使用的配置）。
- RubikSQL 默认配置：`<rubiksql的python包路径>/resources/default_config.yaml`（实际使用的默认配置）。
- RubikSQL 中使用的 AgentHeaven 配置：`./.ahvn/config.yaml` = `~/.rubiksql/.ahvn/config.yaml`（ahvn的一个本地配置）。
- RubikSQL 中使用的 AgentHeaven 默认配置：`<ahvn的python包路径>/resources/ahvn_config.yaml`（安装时覆盖到`~/.rubiksql/.ahvn/config.yaml`）。

这种管理比较混乱（代码中存在很多使用错误的配置），无法支持第三层配置（例如`rubiksql`的本地`ahvn`配置），无法支持多用户并发使用或多数据库不同配置，无法支持配置版本管理。

## 2. 新版配置系统

新版配置系统基于数据库（目前仅支持SQLite，后续扩展支持Postgres或通用的Database类），可支持无限层级的配置（通过scope实现），并且每次修改都会自动版本化，支持查看历史版本和回滚。

具体地，我们将原本的三层配置扩展为了基于scope的系统，通过一个`.`分割的字符串来标识当前的配置层级：`ahvn` (全局配置), `ahvn.<local>` (本地配置)。

因此，在`rubiksql`包中需要使用`ahvn`的配置时，只需要一个新的scope：`ahvn.rubik`，就可以配置RubikSQL中使用的AgentHeaven配置了（然后如果`rubik`需要第三层配置，例如本地`ahvn.rubik.<local>`，或更层的配置，例如用户`ahvn.rubik.app.<user_id>`）。基于scope的配置会自动继承父scope的配置（例如`ahvn.rubik`中的缺省值会继承`ahvn`的配置）。

使用方法与以前相同（`HEAVEN_CM` --重命名-> `CM_AHVN`）：

- （未来）一个入口文件：`~/.ahvn/system.yaml`，包含`config`类的数据库连接，全局唯一，不包含配置。
- （临时）SQLite数据库: `~/.ahvn/config.db`，包含所有scope的配置数据。
    - 当前数据库中仅包含两张表：
        - configs(id, package, scope, version, package_version, data, created_at)
            - 这里每个scope中每个版本的配置都是一行，修改配置时会自动插入新行（不会修改旧数据），version递增，created_at记录时间戳。
        - compatibility(id, package, package_version, version_order, compatible_order)
            - 这里需要在代码中维护所有版本（顺序）及其最早的兼容版本
- 默认配置：`<ahvn的python包路径>/resources/default_config.yaml`，可通过重载`load_default`来修改。

在执行某个配置的查询时，会选取当前package的当前scope链（例如`ahvn.rubik`的scope链包含`ahvn`和`ahvn.rubik`两个scope）中所有兼容版本中，各自取最新版本，然后按照scope链顺序覆盖得到最终的配置。
在执行某个配置的修改时，需要指定scope，自动在当前scope中插入新版本（version递增），并且自动记录当前版本，每个版本都是完整的配置的复制。
可以通过`setup`全局初始化（`reset=True`时删除所有的配置），通过`init`初始化一个具体的scope（`reset=True`时添加一个新的来自默认配置的版本）。
可以通过`compact`来压缩一个scope的所有版本，清除以前版本，只保留最新的版本（id重设为1）。

通过成员函数`scoped(·)`来控制当前scope，默认为package的base_scope。基于scope的使用示例：

```python
from ahvn.utils.basic.config_utils import ConfigManager

CM_AHVN = ConfigManager(
    package="ahvn",                 # 包名（使用名、src文件夹名、import名）
    distribution="agent-heaven",    # pypi包名（pip安装的包名）
    scope="ahvn",                   # 基础scope（默认为包名）
    setup=True                      # 是否在初始化时自动调用 setup(reset=False)
)

print(CM_AHVN.get("core.debug"))        # ahvn.core.debug (default: False)

with CM_AHVN.scoped("demo"):
    print(CM_AHVN.get("core.debug"))    # ahvn.demo.core.debug (default: False)

with CM_AHVN.scoped("demo"):
    CM_AHVN.set("core.debug", True)
    print(CM_AHVN.get("core.debug"))    # ahvn.demo.core.debug (default: True，已经修改)

print(CM_AHVN.get("core.debug"))        # ahvn.core.debug (default: False，因为scope不同，未修改）

CM_AHVN.compact("ahvn.demo")            # 确保demo只有一个版本（多次运行此脚本可能会产生很多版本）
```

如果要接入`rubiksql`，则创建一个新的实例（当前不支持，因为`rubiksql`还未适配）：
```python
CM_RUBIK = ConfigManager(
    package="rubiksql",
    distribution="rubiksql",
    scope="rubik",                  # rubiksql自己的配置，与ahvn完全无关
    setup=True
)

# 当在RubikSQL中使用配置时
CM_RUBIK.get("key", default=...)

# 当在RubikSQL中调用任何AgentHeaven功能（例如LLM）时
from ahvn.utils.llm import LLM

with CM_AHVN.scoped("rubik"):       # 此时访问的配置为`ahvn.rubik`的LLM配置
    llm = LLM(...)                  # 配置修改同理，可以用with，或者可以直接指定scope：`CM_AHVN.set("key", value, scope="ahvn.rubik")`

# 当在RubikSQL中调用通用的AgentHeaven功能时
llm = LLM(...)                       # 此时访问的配置为`ahvn`的LLM配置
```

如果要在`RubikSQL`的应用后端使用，例如某个用户发起对话时：
```python
def chat(..., user_token=...):
    user_id, auth_info = get_user_info(user_token)
    with CM_RUBIK.scoped(f"app.{user_id}"), CM_AHVN.scoped(f"app.{user_id}"):
        # 此时所有rubiksql中的配置访问都是`ahvn.rubik.app.{user_id}`的scope，可以针对不同用户设置不同的配置
        # 而且所有通用的AgentHeaven功能访问的配置都是`ahvn.rubik.app.{user_id}`的scope，可以针对不同用户设置不同的配置
        #   其中`app.{user_id}`来自这里的with，而`rubik`来自之前在CM_AHVN.scoped("rubik")中设置的scope，因此需要保证在rubiksql中所有访问`ahvn`的时候都正确使用了scope
        # 如果用户没有配置AgentHeaven，因为存在四层继承关系（`ahvn` → `ahvn.rubik` → `ahvn.rubik.app` → `ahvn.rubik.app.{user_id}`），则会自动回退到`ahvn.rubik`的配置
        # 此时，内部的`rubiksql`与`ahvn`都完全不感知用户`user_id`的存在，完全通过scope来实现用户隔离，实现无状态服务
        ...
```

注：当前单个scope中传参直接使用多段，例如`app.<user_id>`的用法并未经过严格测试，可能有bug。
