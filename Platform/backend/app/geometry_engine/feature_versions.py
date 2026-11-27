"""声明服务节点可解析的特征版本。只依赖标准库，版本与对应算法随代码一同部署。"""

INITIAL_FEATURE_VERSION = "1.0.0"
SUPPORTED_FEATURE_VERSIONS = frozenset({INITIAL_FEATURE_VERSION})


def require_feature_version(version: str) -> None:
    """检查 version 是否由当前节点实现，无返回值，不支持时抛 ValueError。"""
    if version not in SUPPORTED_FEATURE_VERSIONS:
        raise ValueError("当前节点不支持此特征版本")
