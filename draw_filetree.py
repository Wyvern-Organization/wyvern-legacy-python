# This script is for drawing the file tree from `.`.

import os
import pyperclip    # for clipboard stuff

def draw_filetree(path, level=0):
    """Returns the file tree from the given path as one string."""
    lines = []

    def walk(current_path, current_level):
        lines.append("  " * current_level + os.path.basename(current_path))

        for child in os.listdir(current_path):
            child_path = os.path.join(current_path, child)

            if os.path.isdir(child_path):
                walk(child_path, current_level + 1)
            else:
                lines.append("  " * (current_level + 1) + child)

    walk(path, level)
    return "\n".join(lines)
            
print(draw_filetree("."))

pyperclip.copy(draw_filetree("."))