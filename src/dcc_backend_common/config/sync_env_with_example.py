#!/usr/bin/env python3
import argparse
from pathlib import Path


def parse_env_file(file_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    env_vars = {}
    comments = {}

    if not file_path.exists():
        return env_vars, comments

    with file_path.open() as f:
        lines = f.readlines()

    current_comments = []
    current_var_name = None

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#"):
            current_comments.append(stripped)
        elif stripped and "=" in stripped:
            current_var_name, _ = stripped.split("=", 1)
            current_var_name = current_var_name.strip()
            env_vars[current_var_name] = stripped
            if current_comments:
                comments[current_var_name] = current_comments
                current_comments = []
        else:
            current_comments = []

    return env_vars, comments


def report_extra_variables(extra_vars: set[str]) -> None:
    if extra_vars:
        print("Warning: Variables in .env but not in .env.example:")
        for var in sorted(extra_vars):
            print(f"  - {var}")
        print()


def prepare_missing_vars_content(
    missing_vars: set[str], example_vars: dict[str, str], example_comments: dict[str, list[str]], dry_run: bool
) -> list[str]:
    lines_to_append = []

    for var_name in sorted(missing_vars):
        comment_lines = example_comments.get(var_name, [])
        var_line = example_vars[var_name]

        lines_to_append.append("\n")
        for comment in comment_lines:
            lines_to_append.append(comment + "\n")
        lines_to_append.append(var_line + "\n")
        lines_to_append.append("\n")

        if dry_run:
            for comment in comment_lines:
                print(f"  {comment}")
            print(f"  {var_line}")

    return lines_to_append


def sync_env(example_path: Path, env_path: Path, dry_run: bool = False) -> None:
    example_vars, example_comments = parse_env_file(example_path)
    env_vars, _ = parse_env_file(env_path)

    missing_vars = set(example_vars.keys()) - set(env_vars.keys())
    extra_vars = set(env_vars.keys()) - set(example_vars.keys())

    report_extra_variables(extra_vars)

    if not env_path.exists() and not dry_run:
        env_path.touch()
        print(f"Created {env_path}")

    if missing_vars:
        if dry_run:
            print(f"Dry run: Would append {len(missing_vars)} variables to {env_path}:")
        else:
            print(f"Appending {len(missing_vars)} variables to {env_path}:")

        lines_to_append = prepare_missing_vars_content(missing_vars, example_vars, example_comments, dry_run)

        if not dry_run:
            with env_path.open("a") as f:
                f.writelines(lines_to_append)
            print(f"Successfully appended {len(missing_vars)} variables")
    else:
        print("No missing variables found. .env is up to date.")

    if not missing_vars and not extra_vars:
        print("All variables are in sync.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync .env with .env.example")
    parser.add_argument(
        "--example-path", default=".env.example", help="Path to .env.example file (default: .env.example)"
    )
    parser.add_argument("--env-path", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Preview changes without modifying files")

    args = parser.parse_args()

    example_path = Path(args.example_path)
    env_path = Path(args.env_path)

    if not example_path.exists():
        print(f"Error: {example_path} does not exist")
        return

    sync_env(example_path, env_path, args.dry_run)


if __name__ == "__main__":
    main()
