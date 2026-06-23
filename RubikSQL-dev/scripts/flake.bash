#!/bin/bash
set -e

# Configuration
CODE_DIR="src/ scripts/"
TARGET_DIRS="$CODE_DIR"
LINE_LENGTH=160
FLAKE8_IGNORE="F401,F403,F405,E203,E402,E501,W503,E701"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Help function
show_help() {
    echo "Usage: $0 [OPTIONS] [PATHS...]"
    echo ""
    echo "Options:"
    echo "  -b, --black     Run Black code formatter"
    echo "  -c, --check     Run Black in check mode (don't modify files)"
    echo "  -f, --flake     Run Flake8 linter (default)"
    echo "  -a, --all       Run both Black formatter and Flake8 linter"
    echo "  -p, --paths     Specify paths to check/format (default: $CODE_DIR)"
    echo "  -h, --help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0              # Run Flake8 only"
    echo "  $0 -b           # Format code with Black"
    echo "  $0 -c           # Check code formatting with Black"
    echo "  $0 -a           # Run both Black formatter and Flake8"
    echo "  $0 -a -p src/ tests/  # Run both on specific paths"
}

# Default behavior
RUN_BLACK=false
RUN_BLACK_CHECK=false
RUN_FLAKE=true

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -b|--black)
            RUN_BLACK=true
            RUN_FLAKE=false
            shift
            ;;
        -c|--check)
            RUN_BLACK_CHECK=true
            RUN_FLAKE=false
            shift
            ;;
        -f|--flake)
            RUN_FLAKE=true
            shift
            ;;
        -a|--all)
            RUN_BLACK=true
            RUN_FLAKE=true
            shift
            ;;
        -p|--paths)
            TARGET_DIRS=""
            shift
            while [[ $# -gt 0 ]] && [[ $1 != -* ]]; do
                TARGET_DIRS="$TARGET_DIRS $1"
                shift
            done
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Run Black formatter
if [ "$RUN_BLACK" = true ]; then
    print_status "Running Black code formatter..."
    if black $TARGET_DIRS --line-length=$LINE_LENGTH; then
        print_status "Black formatting completed successfully."
    else
        print_error "Black formatting failed."
        exit 1
    fi
fi

# Run Black check
if [ "$RUN_BLACK_CHECK" = true ]; then
    print_status "Checking code formatting with Black..."
    if black $TARGET_DIRS --line-length=$LINE_LENGTH --check --diff; then
        print_status "Code is properly formatted."
    else
        print_error "Code formatting check failed. Run '$0 -b' to fix formatting."
        exit 1
    fi
fi

# Run Flake8
if [ "$RUN_FLAKE" = true ]; then
    print_status "Running Flake8 linter..."
    if flake8 $TARGET_DIRS --max-line-length=$LINE_LENGTH --ignore=$FLAKE8_IGNORE; then
        print_status "Flake8 linting completed successfully."
    else
        print_error "Flake8 linting found issues."
        exit 1
    fi
fi

print_status "All checks completed!"

