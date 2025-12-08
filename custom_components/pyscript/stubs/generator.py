"""AST-based stubs generator used to build IDE helper files."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import IntEnum, IntFlag, StrEnum
import keyword
import logging
from typing import Any, Literal

from custom_components.pyscript.stubs.pyscript_builtins import StateVal
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions

_LOGGER = logging.getLogger(__name__)

_STATE_BASE_FIELDS = {attr for attr, value in StateVal.__annotations__.items()}
_STATE_CLASS_SUFFIX = "_state"
_STATE_CLASS = "StateVal"
_DOCSTRING_INDENT = " " * 8

SELECTOR_SIMPLE_TYPES = {
    "boolean": "bool",
    "color_rgb": "tuple[int, int, int]",
    "color_temp": "int",
    "config_entry": "str",
    "date": "datetime",
    "datetime": "datetime",
    "entity": "str",
    "icon": "str",
    "object": "Any",
    "state": "str",
    "text": "str",
    "time": "str",
}


@dataclass
class _ServiceField:
    """Describe a Home Assistant service field."""

    name: str
    required: bool
    annotation: ast.expr
    default: ast.expr | None
    description: str | None


class StubsGenerator:
    """Build a pyscript stubs modules using the Python AST."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize stubs generator."""
        self._hass = hass
        self.ignored_identifiers: list[str] = []
        self._classes: dict[str, ast.ClassDef] = {}
        self._domain_attributes: dict[str, dict[str, set[str]]] = {}

    async def build(self) -> str:
        """Return the generated stub body."""

        module_body: list[ast.stmt] = []

        imports = {
            "typing": ["Any", "Literal"],
            "datetime": ["datetime"],
            "pyscript_builtins": [_STATE_CLASS],
        }

        for module, imports in imports.items():
            module_body.append(
                ast.ImportFrom(
                    module=module,
                    level=0,
                    names=[ast.alias(name=imp, asname=None) for imp in imports],
                )
            )

        await self._build_entity_classes()
        await self._build_services()

        # order: entities first, then domains; ignore leading “_”.
        sorted_domains = sorted(
            self._classes.keys(),
            key=lambda s: (
                s[1:].split("_", 1)[0] if s.startswith("_") else s,
                0 if s.startswith("_") else 1,
                s,
            ),
        )

        for domain_id in sorted_domains:
            domain = self._classes[domain_id]
            attributes = self._domain_attributes.get(domain_id)
            if attributes is not None:
                for attr, attr_types in sorted(attributes.items(), reverse=True):
                    ann = None
                    for attr_type in attr_types:
                        if attr_type is None:
                            continue
                        if ann is not None:
                            ann = ast.BinOp(left=ann, op=ast.BitOr(), right=self._name(attr_type))
                        else:
                            ann = self._name(attr_type)

                    if not ann:
                        ann = self._name("Any")
                    domain.body.insert(
                        0,
                        ast.AnnAssign(
                            target=self._name(attr),
                            annotation=ann,
                            value=None,
                            simple=1,
                        ),
                    )

            if len(domain.body) == 0:
                # empty class body
                domain.body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))

            module_body.append(domain)

        module = ast.Module(body=module_body, type_ignores=[])
        ast.fix_missing_locations(module)
        return ast.unparse(module)

    def _get_or_create_class(self, domain_id: str, base_class: str = None) -> ast.ClassDef:
        cls = self._classes.get(domain_id)
        if cls is None:
            cls = ast.ClassDef(
                name=domain_id,
                bases=[] if base_class is None else [self._name(base_class)],
                keywords=[],
                body=[],
                decorator_list=[],
            )
            self._classes[domain_id] = cls
        return cls

    def _collect_entity_atts(self, domain_id: str, entity_id: str) -> None:
        state = self._hass.states.get(f"{domain_id}.{entity_id}")
        if state is None:
            return
        # _LOGGER.debug(f"Collecting entity attributes for {domain_id}.{entity_id}: {state.attributes}")
        entity_attributes = self._domain_attributes.setdefault(self._get_entity_class_name(domain_id), {})

        for attr_key, attr_value in state.attributes.items():
            fqn = f"{domain_id}.{entity_id}.{attr_key}"

            if attr_key in _STATE_BASE_FIELDS:
                continue

            if not self._is_identifier(attr_key, fqn):
                continue
            value_type = self._get_entity_attribute_type(fqn, attr_value)

            entity_attributes.setdefault(attr_key, set()).add(value_type)

    def _get_entity_class_name(self, domain_id: str) -> str:
        return f"_{domain_id}{_STATE_CLASS_SUFFIX}"

    async def _build_entity_classes(self):
        for entity in er.async_get(self._hass).entities.values():
            if entity.disabled:
                continue

            domain_id, entity_id = split_entity_id(entity.entity_id)

            if not self._is_identifier(entity_id, entity.entity_id):
                continue

            self._collect_entity_atts(domain_id, entity_id)

            self._get_or_create_class(domain_id).body.append(
                ast.AnnAssign(
                    target=self._name(entity_id),
                    annotation=self._name(self._get_entity_class_name(domain_id)),
                    value=None,  # ast.Constant(value=Ellipsis),
                    simple=1,
                )
            )

            self._get_or_create_class(self._get_entity_class_name(domain_id), _STATE_CLASS)

    async def _build_services(self):
        def process_fields(fields: dict[str, Any]) -> list[_ServiceField]:
            result: list[_ServiceField] = []
            for field_name, field in (fields.get("fields") or {}).items():
                if field_name == "advanced_fields":
                    result.extend(process_fields(field))
                    continue
                definition = self._describe_service_field(service_id, field_name, field)
                if definition is not None:
                    result.append(definition)
            return result

        descriptions = await async_get_all_descriptions(self._hass)
        for domain_id, services in descriptions.items():

            domain_class = self._get_or_create_class(domain_id)
            for service_id, payload in services.items():
                if not self._is_identifier(service_id, f"{domain_id}.{service_id}"):
                    continue

                _LOGGER.debug("Building service %s.%s payload: %s", domain_id, service_id, payload)

                field_nodes = sorted(
                    process_fields(payload),
                    key=lambda x: not x.required,
                )

                has_target = "target" in payload
                entity_class = self._get_entity_class_name(domain_id)
                if has_target and entity_class in self._classes:
                    entity_service = await self._create_service_function(
                        service_id, field_nodes, payload, "entity"
                    )
                    self._get_or_create_class(entity_class).body.append(entity_service)

                service = await self._create_service_function(service_id, field_nodes, payload, "service")
                domain_class.body.append(service)

    async def _create_service_function(
        self,
        service_id: str,
        field_nodes: list[_ServiceField],
        payload: dict[str, Any],
        def_type: Literal["entity", "service"] = "service",
    ) -> ast.FunctionDef:
        """Create a function definition describing the service signature."""

        args: list[ast.arg] = []
        kwonlyargs: list[ast.arg] = []
        kw_defaults: list[ast.expr] = []
        decorator_list: list[ast.expr] = []

        has_target = "target" in payload

        if def_type == "service":
            decorator_list.append(self._name("staticmethod"))

            if has_target:
                field_nodes = [
                    _ServiceField(
                        name="entity_id",
                        annotation=self._name("str"),
                        required=True,
                        default=None,
                        description="Entity ID",
                    )
                ] + field_nodes

        elif def_type == "entity":
            args.append(ast.arg(arg="self"))

        if def_type == "entity" and len(field_nodes) == 1:  # simple calling with 1 arg service
            args.append(ast.arg(arg=field_nodes[0].name, annotation=field_nodes[0].annotation))
        else:
            for field in field_nodes:
                kwonlyargs.append(ast.arg(arg=field.name, annotation=field.annotation))
                kw_defaults.append(field.default)

        if "response" in payload:
            returns = ast.Subscript(
                value=self._name("dict"),
                slice=ast.Tuple(elts=[self._name("str"), self._name("Any")]),
            )
        else:
            returns = None

        body: list[ast.stmt] = []

        docstring_value = self._build_docstring(payload.get("description"), field_nodes)
        if docstring_value:
            body.append(ast.Expr(value=ast.Constant(value=docstring_value)))
        body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))

        service_function = ast.FunctionDef(
            name=service_id,
            args=ast.arguments(
                posonlyargs=[],
                args=args,
                vararg=None,
                kwonlyargs=kwonlyargs,
                kw_defaults=kw_defaults,
                kwarg=None,
                defaults=[],
            ),
            body=body,
            decorator_list=decorator_list,
            returns=returns,
        )
        return service_function

    def _get_entity_attribute_type(self, fqn: str, value: Any) -> str | None:
        if value is None:
            return None
        t = type(value)
        if t in (bool, int, float, str, list, dict, set, tuple):
            return t.__qualname__
        if t.__module__ == "datetime" and t.__qualname__ == "datetime":
            return t.__qualname__
        if isinstance(value, StrEnum):
            return "str"
        if isinstance(value, (IntEnum, IntFlag)):
            return "int"
        _LOGGER.debug("Attribute %s type %s unknown, value: %s", fqn, t, value)
        return None

    def _describe_service_field(
        self,
        service: str,
        field_name: str,
        field: dict[str, Any],
    ) -> _ServiceField | None:
        fqn = f"{service}({field_name})"
        if not self._is_identifier(field_name, fqn):
            return None

        try:
            annotation = self._selector_annotation(field.get("selector"))

            is_required = field.get("required") is True

            default_expr = None
            default_value = field.get("default")
            if default_value is not None and isinstance(default_value, (int, float, str, bool)):
                default_expr = ast.Constant(value=default_value)

            if not is_required:
                if default_expr is None:
                    if annotation is not None:
                        # add | None for optional fields without default value
                        annotation = ast.BinOp(left=annotation, op=ast.BitOr(), right=ast.Constant(None))
                    default_expr = ast.Constant(value=None)

            description = field.get("description") or ""
            if example := field.get("example"):
                description += f" Example: {example}"

            return _ServiceField(
                name=field_name,
                required=is_required,
                annotation=annotation,
                default=default_expr,
                description=description,
            )
        except Exception:
            _LOGGER.exception("Incorrect method description %s: %s", fqn, field)
            return None

    def _selector_annotation(self, selector: dict[str, Any] | None) -> ast.expr | None:
        if not selector:
            return None
        for selector_id, selector_value in selector.items():
            if selector_type := SELECTOR_SIMPLE_TYPES.get(selector_id):
                return self._name(selector_type)
            if selector_id == "number":
                if selector_value == "any":
                    return self._name("float")
                if isinstance(selector_value, dict) and selector_value.get("mode") == "box":
                    return self._name("float")
                return self._name("int")
            if selector_id == "select":
                options = []
                if isinstance(selector_value, dict):
                    options = selector_value.get("options") or []
                literals = [ast.Constant(value="")]
                for option in options:
                    value = option.get("value") if isinstance(option, dict) else option
                    literals.append(ast.Constant(value=value))
                return ast.Subscript(
                    value=self._name("Literal"),
                    slice=ast.Tuple(elts=literals),
                )
        _LOGGER.debug("Selector annotation unknown %s", selector)
        return None

    def _is_identifier(self, value: str, fqn: str) -> bool:
        valid = value.isidentifier() and not keyword.iskeyword(value)
        if not valid:
            self.ignored_identifiers.append(fqn)
            _LOGGER.debug("Invalid python identifier %s (%s)", value, fqn)
        return valid

    def _name(self, identifier: str, ctx: ast.expr_context | None = None) -> ast.expr:
        if ctx is None:
            ctx = ast.Load()
        return ast.Name(id=identifier, ctx=ctx)

    def _build_docstring(self, description: str | None, fields: list[_ServiceField]) -> str | None:
        docstring = description.strip() if description else ""
        docstring = docstring.replace("\n", f"\n{_DOCSTRING_INDENT}")
        first_arg = True
        for field in fields:
            if not field.description:
                continue
            if first_arg:
                docstring += f"\n\n{_DOCSTRING_INDENT}Args:"
                first_arg = False
            f_desc = field.description.replace("\n", f"\n{_DOCSTRING_INDENT * 2}")
            docstring += f"\n{_DOCSTRING_INDENT}    {field.name}: {f_desc}"
        return docstring
