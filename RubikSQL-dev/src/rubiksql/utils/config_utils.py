__all__ = [
    "RubikSQLConfigManager",
    "RUBIK_CM",
    "HEAVEN_CM",
    "rpj",
]

from .basic_utils import *

_RUBIK_COPY_RESOURCES = [
    "prompts",
]


class RubikSQLConfigManager(ConfigManager):
    name = "rubiksql"
    package = "rubiksql"

    def __post_init__(self):
        HEAVEN_CM.set_cwd(self.root)

    def setup(self, reset: bool = False) -> bool:
        """Setup RubikSQL global configuration.

        Args:
            reset (bool): Whether to reset to default values. Defaults to False.

        Returns:
            bool: True if setup is successful, False otherwise.
        """
        HEAVEN_CM.setup(reset=False)
        HEAVEN_CM.set_cwd(self.root)
        HEAVEN_CM.init(reset=reset)
        super().setup(reset=reset)

        # Load ahvn defaults from package
        defaults = load_yaml(rpj("& configs/ahvn_config.yaml", abs=True))
        if reset:
            # Full reset: overwrite everything with defaults
            HEAVEN_CM.set(key_path=None, value=defaults, level="local")
        else:
            HEAVEN_CM.set(key_path=None, value=dmerge([defaults, HEAVEN_CM.get(level="local")]), level="local")

        HEAVEN_CM.set("prompts.scan[-1]", rpj("& prompts/", abs=True), level="local")

        # for folder in list_dirs(rpj("&", abs=True)):
        #     if folder.startswith("_"):
        #         continue
        #     if folder not in _RUBIK_COPY_RESOURCES:  # Whitelist folders for now
        #         continue
        #     copy_dir(rpj(f"& {folder}", abs=True), hpj(f"> {folder}/", abs=True), mode=("replace" if reset else "skip"))
        return True


RUBIK_CM = RubikSQLConfigManager(name="rubiksql", package="rubiksql")
HEAVEN_CM.set_cwd(RUBIK_CM.root)


def rpj(*args: List[str], abs: bool = False, cm: Optional[RubikSQLConfigManager] = None) -> str:
    """Get path to RubikSQL resource.

    Args:
        *args: Path components within the RubikSQL resource directory.
        abs: Whether to return an absolute path. Defaults to False.
        cm: ConfigManager instance to use. Defaults to RUBIK_CM.

    Returns:
        Full path to the specified resource.
    """
    if cm is None:
        cm = RUBIK_CM
    return hpj(*args, abs=abs, cm=cm)
