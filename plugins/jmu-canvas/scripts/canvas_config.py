"""
Shared utility for managing Canvas API configuration (API key and course ID).
Handles command line arguments and config file caching similar to download_text_submissions.py.
"""

import argparse
import configparser
import sys
import pathlib
from canvasapi import Canvas


def get_datadir() -> pathlib.Path:
    """
    Returns a parent directory path
    where persistent application data can be stored.

    # linux: ~/.local/share
    # macOS: ~/Library/Application Support
    # windows: C:/Users/<USER>/AppData/Roaming
    """
    home = pathlib.Path.home()

    if sys.platform == "win32":
        return home / "AppData/Roaming"
    elif sys.platform == "linux":
        return home / ".local/share"
    elif sys.platform == "darwin":
        return home / "Library/Application Support"


def get_config(config_name="config.ini"):
    """Return the Path object for the config file, creating an empty file
    if it doesn't already exist.
    """
    config_dir = get_datadir() / "bulk_canvas_actions"
    try:
        config_dir.mkdir(parents=True)
    except FileExistsError:
        pass
    config_path = config_dir / config_name

    if not config_path.is_file():
        config_path.touch()

    return config_path


def setup_canvas_config(parser=None, require_course_id=True, canvas_url="https://canvas.jmu.edu"):
    """
    Set up Canvas configuration from command line arguments and/or config file.
    
    Args:
        parser: Optional existing ArgumentParser. If None, creates a new one.
        require_course_id: Whether course ID is required (default True)
        canvas_url: Canvas instance URL (default https://canvas.jmu.edu)
    
    Returns:
        tuple: (canvas_api_object, course_id, args)
    """
    if parser is None:
        parser = argparse.ArgumentParser(description="Canvas script with configurable API key and course ID.")
    
    # Add standard Canvas config arguments
    parser.add_argument("--course-id", help="Canvas course id for this run (optional if a "
                        "default is saved). Saved as the default only if none is saved "
                        "yet, or with --save-course-id.")
    parser.add_argument("--save-course-id", action="store_true",
                        help="also save --course-id as the default for later runs")
    parser.add_argument("--key-file", help="file that contains your Canvas key (oauth)")
    parser.add_argument("--key", help="your Canvas key (oauth)")
    
    args = parser.parse_args()
    
    config_path = get_config("config.ini")
    config = configparser.ConfigParser()
    config.read(config_path)
    
    # Ensure DEFAULT section exists
    if "DEFAULT" not in config:
        config["DEFAULT"] = {}
    
    # Check if we have an API key
    if args.key is None and args.key_file is None and "key" not in config["DEFAULT"]:
        print("No default key is cached. Must specify either a key or a key filename")
        sys.exit(1)
    
    update = False
    
    # Handle API key
    if args.key_file is not None:
        with open(args.key_file, "r") as f:
            config["DEFAULT"]["key"] = f.readline().rstrip()
            update = True
    elif args.key is not None:
        config["DEFAULT"]["key"] = args.key
        update = True
    
    # Handle course ID. A --course-id is for this run only: silently replacing
    # the saved default means the next command run without --course-id
    # targets a course the user never chose.
    saved_course_id = config["DEFAULT"].get("course_id")
    if args.course_id is not None:
        if saved_course_id is None or args.save_course_id:
            config["DEFAULT"]["course_id"] = args.course_id
            update = True
        elif saved_course_id != args.course_id:
            print(f"Using course {args.course_id} for this run; the saved default "
                  f"({saved_course_id}) is unchanged. Add --save-course-id to "
                  "replace it.", file=sys.stderr)
    elif args.save_course_id:
        print("--save-course-id needs --course-id", file=sys.stderr)
        sys.exit(1)
    
    # Update config file if needed
    if update:
        print(f"Updating {config_path}")
        with open(config_path, "w") as configfile:
            config.write(configfile)
    
    # Get API key
    api_key = config["DEFAULT"]["key"]
    
    # Get course ID
    course_id = None
    if args.course_id is not None:
        course_id = args.course_id
    elif "course_id" in config["DEFAULT"]:
        course_id = config["DEFAULT"]["course_id"]
    elif require_course_id:
        print("No course ID provided and none found in config file")
        sys.exit(1)
    
    # Create Canvas API object
    canvas = Canvas(canvas_url, api_key)
    
    return canvas, course_id, args


def get_canvas_and_course(parser=None, canvas_url="https://canvas.jmu.edu"):
    """
    Convenience function that returns Canvas API object and Course object.
    
    Args:
        parser: Optional existing ArgumentParser. If None, creates a new one.
        canvas_url: Canvas instance URL (default https://canvas.jmu.edu)
    
    Returns:
        tuple: (canvas_api_object, course_object, args)
    """
    canvas, course_id, args = setup_canvas_config(parser, require_course_id=True, canvas_url=canvas_url)
    course = canvas.get_course(course_id)
    return canvas, course, args


def setup_canvas_config_gui(canvas_url="https://canvas.jmu.edu"):
    """
    Set up Canvas configuration for GUI applications from config file only.
    Returns None values if configuration is not available.
    
    Args:
        canvas_url: Canvas instance URL (default https://canvas.jmu.edu)
    
    Returns:
        tuple: (canvas_api_object, course_id) or (None, None) if config not available
    """
    config_path = get_config("config.ini")
    config = configparser.ConfigParser()
    config.read(config_path)
    
    # Ensure DEFAULT section exists
    if "DEFAULT" not in config:
        return None, None
    
    # Check if we have an API key
    if "key" not in config["DEFAULT"]:
        return None, None
    
    # Check if we have a course ID
    if "course_id" not in config["DEFAULT"]:
        return None, None
    
    # Get configuration values
    api_key = config["DEFAULT"]["key"]
    course_id = config["DEFAULT"]["course_id"]
    
    # Create Canvas API object
    canvas = Canvas(canvas_url, api_key)
    
    return canvas, course_id
