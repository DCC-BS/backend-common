#!/usr/bin/env python3
import argparse
import importlib
from pathlib import Path

from pydantic import BaseModel


def generate_env_example(model_class: type[BaseModel]) -> str:
    lines = []

    for field_name, field_info in model_class.model_fields.items():
        if (
            field_info.json_schema_extra
            and isinstance(field_info.json_schema_extra, dict)
            and field_info.json_schema_extra.get("exclude_from_env")
        ):
            continue

        description = field_info.description or ""

        value = "TODO" if field_info.is_required() else str(field_info.default)

        env_var_name = field_name.upper()
        lines.append(f"# {description}")
        lines.append(f"{env_var_name}={value}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate .env.example from a Pydantic model")
    parser.add_argument("model_path", help="Module path to the model (e.g., src.text_mate_backend.utils.configuration)")
    parser.add_argument("class_name", help="Name of the Pydantic model class")
    parser.add_argument("-o", "--output", default=".env.example", help="Output file path (default: .env.example)")

    args = parser.parse_args()

    module = importlib.import_module(args.model_path)
    model_class = getattr(module, args.class_name)

    if not issubclass(model_class, BaseModel):
        raise TypeError(f"{args.class_name} is not a Pydantic BaseModel")

    content = generate_env_example(model_class)
    output_path = Path(args.output)
    output_path.write_text(content)
    print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
