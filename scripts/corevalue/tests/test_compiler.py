"""Compile the real descriptors in process and reject unsupported shapes before emission."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scripts.corevalue.api import (
    GenerationError,
    Options,
    compile_boundary,
    compile_files,
    compile_values,
)
from scripts.corevalue.tests.api import MapValue, encode
from vs_proto.api import core_control as core_state
from vs_proto.api import core_leaves, core_value_options, validation_rules

if TYPE_CHECKING:
    from collections.abc import Callable

    from protobuf import DescFile


@pytest.mark.parametrize("compiler", [compile_values, compile_boundary])
@pytest.mark.parametrize(
    "file", [core_state.ControlInput.desc().file, encode(MapValue()).desc().file]
)
def test_the_real_schema_compiles_to_complete_python(
    compiler: Callable[[DescFile], str], file: DescFile
) -> None:
    source = compiler(file)
    compile(source, "<core-values>", "exec")
    assert source == compiler(file)


@given(reserved=st.sampled_from(["model_fields", "model_copy", "model_dump_json", "_hash"]))
def test_constructor_method_collisions_are_rejected_by_field_name(reserved: str) -> None:
    original = core_state.ControlInput.desc()
    bad_field = replace(original.fields[0], name=reserved, local_name=reserved)
    message = replace(original, fields=[bad_field])
    file = replace(original.file, messages=[message])
    with pytest.raises(GenerationError, match=reserved):
        compile_values(file)


def test_colliding_generated_names_fail_before_a_file_is_emitted() -> None:
    original = core_state.ControlInput.desc()
    file = replace(original.file, messages=[original, original])
    with pytest.raises(GenerationError, match="collision: ControlInput"):
        compile_values(file)


def test_representation_metadata_is_not_emitted_as_core_state() -> None:
    file = core_value_options.ValueMessageOptions.desc().file
    assert compile_files([file], Options()) == ()


@pytest.mark.parametrize("compiler", [compile_values, compile_boundary])
@pytest.mark.parametrize(
    "expression",
    [
        "this.requests.reduce(x, y, x)",
        "this.requests.all(x, x.root.matches(regex))",
        "this.requests.all(x, this.requests.filter(y, y.root == x.root).unknown())",
        "this.requests.all(x, x.root == 'unbound')",
    ],
)
def test_unsupported_list_rules_fail_both_compilers_by_message_name(
    compiler: Callable[[DescFile], str], expression: str
) -> None:
    original = core_leaves.WaitOn.desc()
    proto = type(original.proto).from_binary(original.proto.to_binary())
    proto.options[validation_rules.ext_message] = validation_rules.MessageRules(
        cel=[validation_rules.Rule(id="unsupported", message="unsupported", expression=expression)]
    )
    message = replace(original, proto=proto)
    with pytest.raises(GenerationError, match="WaitOn"):
        compiler(replace(original.file, messages=[message]))


@pytest.mark.parametrize("compiler", [compile_values, compile_boundary])
def test_the_shared_leaves_compile_with_all_constructor_rules(
    compiler: Callable[[DescFile], str],
) -> None:
    source = compiler(core_leaves.WaitOn.desc().file)
    compile(source, "<shared-leaves>", "exec")
    assert source == compiler(core_leaves.WaitOn.desc().file)


@pytest.mark.parametrize("compiler", [compile_values, compile_boundary])
@pytest.mark.parametrize("default", ["{}", "[]", '"1"', "true", "not-json"])
def test_unrepresentable_defaults_fail_before_either_output_is_emitted(
    compiler: Callable[[DescFile], str], default: str
) -> None:
    original = core_leaves.EvaluationHistoryCursor.desc()
    field = original.fields[0]
    proto = type(field.proto).from_binary(field.proto.to_binary())
    proto.options[core_value_options.ext_core_field] = core_value_options.ValueFieldOptions(
        integer=True, default_json=default
    )
    message = replace(original, fields=[replace(field, proto=proto), *original.fields[1:]])
    with pytest.raises(GenerationError, match="EvaluationHistoryCursor"):
        compiler(replace(original.file, messages=[message]))


@pytest.mark.parametrize("compiler", [compile_values, compile_boundary])
@given(alias=st.sampled_from(["Count", "WaitOn", "bad-name", "lowercase"]))
def test_scalar_aliases_reject_collisions_and_invalid_names(
    compiler: Callable[[DescFile], str], alias: str
) -> None:
    original = core_leaves.WaitOn.desc()
    field = original.fields[-1]
    proto = type(field.proto).from_binary(field.proto.to_binary())
    proto.options[core_value_options.ext_core_field] = core_value_options.ValueFieldOptions(
        aliases=[alias]
    )
    message = replace(original, fields=[*original.fields[:-1], replace(field, proto=proto)])
    with pytest.raises(GenerationError, match="WaitOn"):
        compiler(replace(original.file, messages=[message]))
