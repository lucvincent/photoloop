#!/bin/bash
#
# PhotoLoop Installation Script
# Installs PhotoLoop on Raspberry Pi
#
# Usage:
#   curl -sSL https://example.com/install.sh | sudo bash
#   or
#   sudo bash install.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="/opt/photoloop"
CONFIG_DIR="/etc/photoloop"
CACHE_DIR="/var/lib/photoloop"
LOG_DIR="/var/log/photoloop"

# Auto-detect the user who invoked sudo, or fall back to 'pi'
if [ -n "$SUDO_USER" ]; then
    SERVICE_USER="$SUDO_USER"
elif [ -n "$USER" ] && [ "$USER" != "root" ]; then
    SERVICE_USER="$USER"
else
    SERVICE_USER="pi"
fi

echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                PhotoLoop Installer                           ║"
echo "║              Raspberry Pi Photo Frame                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root (use sudo)${NC}"
    exit 1
fi

# Check if running on Raspberry Pi (optional)
if [ -f /proc/device-tree/model ]; then
    model=$(cat /proc/device-tree/model)
    echo -e "${GREEN}Detected: $model${NC}"
fi

echo ""
echo "This script will install PhotoLoop with the following settings:"
echo "  - Installation directory: $INSTALL_DIR"
echo "  - Configuration: $CONFIG_DIR/config.yaml"
echo "  - Photo cache: $CACHE_DIR/cache/"
echo "  - Logs: $LOG_DIR/"
echo "  - Service user: $SERVICE_USER"
echo ""
read -p "Continue with installation? [Y/n] " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]?$ ]]; then
    echo "Installation cancelled."
    exit 0
fi

echo ""
echo -e "${YELLOW}[1/7] Installing system dependencies...${NC}"

# Update package list
apt-get update

# Install required packages (excluding chromium for now)
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-pygame \
    libsdl2-dev \
    libsdl2-image-dev \
    libsdl2-mixer-dev \
    libsdl2-ttf-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    fonts-dejavu-core \
    cec-utils

# Install Chromium - package name varies by OS version
# Raspberry Pi OS Bookworm (Debian 12+) uses 'chromium'
# Older versions use 'chromium-browser'
echo -e "${YELLOW}Installing Chromium browser...${NC}"
if apt-cache show chromium &>/dev/null; then
    apt-get install -y chromium
    echo -e "${GREEN}Installed 'chromium' package.${NC}"
elif apt-cache show chromium-browser &>/dev/null; then
    apt-get install -y chromium-browser
    echo -e "${GREEN}Installed 'chromium-browser' package.${NC}"
else
    echo -e "${RED}Warning: Could not find chromium package. Please install manually.${NC}"
fi

# Install ChromeDriver - try multiple approaches
echo -e "${YELLOW}Installing ChromeDriver...${NC}"
CHROMEDRIVER_INSTALLED=false

# Method 1: Try chromium-chromedriver package (older systems)
if apt-cache show chromium-chromedriver &>/dev/null; then
    apt-get install -y chromium-chromedriver
    CHROMEDRIVER_INSTALLED=true
fi

# Method 2: Try chromium-driver package (Debian Bookworm)
if [ "$CHROMEDRIVER_INSTALLED" = false ] && apt-cache show chromium-driver &>/dev/null; then
    apt-get install -y chromium-driver
    CHROMEDRIVER_INSTALLED=true
fi

# Method 3: Check if chromedriver is bundled with chromium
if [ "$CHROMEDRIVER_INSTALLED" = false ]; then
    if command -v chromedriver &>/dev/null; then
        echo -e "${GREEN}ChromeDriver found (bundled with Chromium).${NC}"
        CHROMEDRIVER_INSTALLED=true
    elif [ -f /usr/lib/chromium/chromedriver ]; then
        # Create symlink if chromedriver exists but isn't in PATH
        ln -sf /usr/lib/chromium/chromedriver /usr/local/bin/chromedriver
        echo -e "${GREEN}ChromeDriver symlink created.${NC}"
        CHROMEDRIVER_INSTALLED=true
    elif [ -f /usr/lib/chromium-browser/chromedriver ]; then
        ln -sf /usr/lib/chromium-browser/chromedriver /usr/local/bin/chromedriver
        echo -e "${GREEN}ChromeDriver symlink created.${NC}"
        CHROMEDRIVER_INSTALLED=true
    fi
fi

if [ "$CHROMEDRIVER_INSTALLED" = false ]; then
    echo -e "${YELLOW}Warning: ChromeDriver not found in packages.${NC}"
    echo -e "${YELLOW}Attempting to download ChromeDriver...${NC}"

    # Get Chromium version
    if command -v chromium &>/dev/null; then
        CHROME_VERSION=$(chromium --version | grep -oP '\d+' | head -1)
    elif command -v chromium-browser &>/dev/null; then
        CHROME_VERSION=$(chromium-browser --version | grep -oP '\d+' | head -1)
    fi

    if [ -n "$CHROME_VERSION" ]; then
        echo -e "${YELLOW}Note: You may need to manually install ChromeDriver for Chromium $CHROME_VERSION${NC}"
        echo -e "${YELLOW}Try: pip install chromedriver-autoinstaller (installed in venv later)${NC}"
    fi
fi

echo -e "${GREEN}System dependencies installed.${NC}"

echo ""
echo -e "${YELLOW}[2/7] Creating directories...${NC}"

# Create directories
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$CACHE_DIR/cache"
mkdir -p "$LOG_DIR"

# Set ownership
chown -R "$SERVICE_USER:$SERVICE_USER" "$CACHE_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR"

echo -e "${GREEN}Directories created.${NC}"

echo ""
echo -e "${YELLOW}[3/7] Copying application files...${NC}"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy application files
if [ -d "$SCRIPT_DIR/src" ]; then
    # Installing from source directory
    cp -r "$SCRIPT_DIR/src" "$INSTALL_DIR/photoloop/"
    mkdir -p "$INSTALL_DIR/photoloop"
    cp "$SCRIPT_DIR/src/"*.py "$INSTALL_DIR/photoloop/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR/src/"* "$INSTALL_DIR/photoloop/"
    cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

    # Create proper package structure
    mkdir -p "$INSTALL_DIR/photoloop/src"
    cp -r "$SCRIPT_DIR/src/"* "$INSTALL_DIR/photoloop/src/"
    touch "$INSTALL_DIR/photoloop/__init__.py"

    # Copy face detection model
    if [ -d "$SCRIPT_DIR/models" ]; then
        mkdir -p "$INSTALL_DIR/models"
        cp -r "$SCRIPT_DIR/models/"* "$INSTALL_DIR/models/"
        echo -e "${GREEN}Face detection model installed.${NC}"
    fi
else
    echo -e "${RED}Error: Source files not found. Run this script from the photoloop directory.${NC}"
    exit 1
fi

echo -e "${GREEN}Application files copied.${NC}"

echo ""
echo -e "${YELLOW}[4/7] Creating Python virtual environment...${NC}"

# Create virtual environment
python3 -m venv "$INSTALL_DIR/venv"

# Activate and install dependencies
source "$INSTALL_DIR/venv/bin/activate"

# Upgrade pip
pip install --upgrade pip

# Install requirements
pip install -r "$INSTALL_DIR/requirements.txt"

deactivate

echo -e "${GREEN}Python environment created.${NC}"

echo ""
echo -e "${YELLOW}[5/7] Installing configuration...${NC}"

# Copy default config if it doesn't exist
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    if [ -f "$SCRIPT_DIR/config.yaml" ]; then
        cp "$SCRIPT_DIR/config.yaml" "$CONFIG_DIR/config.yaml"
        echo -e "${GREEN}Default configuration installed.${NC}"
    else
        # Create minimal config
        cat > "$CONFIG_DIR/config.yaml" << 'EOF'
# PhotoLoop Configuration
# See documentation for all available options

albums: []
#  - url: "https://photos.app.goo.gl/YOUR_ALBUM_URL"
#    name: "My Album"

display:
  photo_duration_seconds: 30
  transition_type: "fade"
  order: "random"

schedule:
  enabled: true
  off_hours_mode: "black"
  weekday:
    start_time: "07:00"
    end_time: "22:00"
  weekend:
    start_time: "08:00"
    end_time: "23:00"

cache:
  directory: "/var/lib/photoloop/cache"
  max_size_mb: 1000

web:
  enabled: true
  port: 8080
  host: "0.0.0.0"
EOF
        echo -e "${GREEN}Minimal configuration created.${NC}"
    fi
else
    echo -e "${YELLOW}Configuration already exists, not overwriting.${NC}"
fi

echo ""
echo -e "${YELLOW}[6/7] Installing systemd service...${NC}"

# Copy service file
cp "$SCRIPT_DIR/photoloop.service" /etc/systemd/system/photoloop.service

# Update the service file with the correct user
sed -i "s/^User=.*/User=$SERVICE_USER/" /etc/systemd/system/photoloop.service
sed -i "s/^Group=.*/Group=$SERVICE_USER/" /etc/systemd/system/photoloop.service
echo -e "${GREEN}Service configured to run as user: $SERVICE_USER${NC}"

# Reload systemd
systemctl daemon-reload

# Enable service (but don't start yet)
systemctl enable photoloop

echo -e "${GREEN}Systemd service installed and enabled.${NC}"

echo ""
echo -e "${YELLOW}[7/7] Creating CLI command...${NC}"

# Create CLI wrapper script
cat > /usr/local/bin/photoloop << 'EOF'
#!/bin/bash
/opt/photoloop/venv/bin/python -m photoloop.src.cli "$@"
EOF

chmod +x /usr/local/bin/photoloop

echo -e "${GREEN}CLI command installed.${NC}"

echo ""
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║            Installation Complete!                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo ""
echo "Next steps:"
echo ""
echo "1. Edit your configuration:"
echo "   sudo nano $CONFIG_DIR/config.yaml"
echo ""
echo "2. Add your Google Photos album URLs to the 'albums' section"
echo ""
echo "3. Start PhotoLoop:"
echo "   sudo systemctl start photoloop"
echo ""
echo "4. Check status:"
echo "   sudo systemctl status photoloop"
echo "   photoloop status"
echo ""
echo "5. Access web interface:"
echo "   http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo "Commands:"
echo "  photoloop status    - Show current status"
echo "  photoloop start     - Force slideshow on"
echo "  photoloop stop      - Force slideshow off"
echo "  photoloop resume    - Resume schedule"
echo "  photoloop sync      - Sync albums now"
echo ""
echo "Service commands:"
echo "  sudo systemctl start photoloop    - Start service"
echo "  sudo systemctl stop photoloop     - Stop service"
echo "  sudo systemctl restart photoloop  - Restart service"
echo "  sudo journalctl -u photoloop -f   - View logs"
echo ""
echo -e "${GREEN}Enjoy your photo frame!${NC}"
