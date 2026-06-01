"""
Compatibility patches for running the legacy Flask stack on newer Python.
"""


def patch_legacy_werkzeug_ast():
    """
    Werkzeug 2.0.x still references deprecated ast aliases removed in newer
    Python versions. Patch them before Flask imports Werkzeug.
    """
    import ast

    if not hasattr(ast, "Str"):
        ast.Str = ast.Constant
    if not hasattr(ast, "Num"):
        ast.Num = ast.Constant
    if not hasattr(ast, "NameConstant"):
        ast.NameConstant = ast.Constant
