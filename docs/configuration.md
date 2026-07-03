# Configuration Guide

Complete guide to configuring the Mortgage Data Manager.

## Table of Contents

- [Overview](#overview)
- [Environment Variables](#environment-variables)
- [Configuration Files](#configuration-files)
- [Directory Structure](#directory-structure)
- [Per-Subpackage Configuration](#per-subpackage-configuration)
- [Common Configuration Patterns](#common-configuration-patterns)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Mortgage Data Manager uses a hierarchical configuration system:

1. **Default values** - Built into the code
2. **Environment variables** - Override defaults
3. **Configuration files** - Workflow-specific settings (matching, etc.)

All configuration is managed through Python's `decouple` library, which reads from `.env` files or environment variables.

---

## Environment Variables

### Core Configuration

These variables control the base directories for all subpackages.

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `MORTGAGE_DATA_PROJECT_DIR` | Project root directory | Auto-detected | `/Users/you/mortgage_data_manager` |
| `MORTGAGE_DATA_DIR` | Root data directory | `{PROJECT_DIR}/data` | `/data/mortgage_data` |
| `MORTGAGE_OUTPUT_DIR` | Root output directory | `{PROJECT_DIR}/output` | `/output/mortgage_data` |
| `MORTGAGE_DATA_CROSSWALK_DIR` | Crosswalk output directory | `{PROJECT_DIR}/crosswalk` | `/data/crosswalk` |

**Example `.env` file:**

```bash
# .env
MORTGAGE_DATA_PROJECT_DIR=/Users/username/mortgage_data_manager
MORTGAGE_DATA_DIR=/data/mortgage_data
MORTGAGE_OUTPUT_DIR=/output/mortgage_data
```

### HMDA Configuration

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `HMDA_DATA_DIR` | HMDA data directory | `{MORTGAGE_DATA_DIR}/hmda` | `/data/hmda` |

### FHA Configuration

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `FHA_DATA_DIR` | FHA data directory | `{MORTGAGE_DATA_DIR}/fha` | `/data/fha` |

### MBS Configuration

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `MBS_DATA_DIR` | MBS data directory | `{MORTGAGE_DATA_DIR}/mbs` | `/data/mbs` |

### FHLB Configuration

FHLB is a subpackage with its own data directory under `{MORTGAGE_DATA_DIR}/fhlb/`. Like every subpackage, it supports per-directory environment overrides of the form `FHLB_DATA_DIR`, `FHLB_RAW_DIR`, `FHLB_BRONZE_DIR`, `FHLB_SILVER_DIR`, `FHLB_GOLD_DIR` (e.g. `FHLB_RAW_DIR=/custom/fhlb/raw`).

### Matching Configuration

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `MATCHING_DATA_DIR` | Matching workflows data directory | `{MORTGAGE_DATA_DIR}/matching` | `/data/matching` |
| `MATCHING_OUTPUT_DIR` | Matching output directory | `{MATCHING_DATA_DIR}/output` | `/data/matching/output` |
| `MATCHING_CACHE_DIR` | Matching cache directory | `{MATCHING_DATA_DIR}/cache` | `/data/matching/cache` |

---

## Configuration Files

### Creating a .env File

Create a `.env` file in your project root:

```bash
# .env
# Base configuration
MORTGAGE_DATA_DIR=/data/mortgage_data

# Subpackage-specific (optional, uses defaults if not set)
HMDA_DATA_DIR=/data/hmda
FHA_DATA_DIR=/data/fha
MATCHING_DATA_DIR=/data/matching
```

### Environment Variable Priority

1. **System environment variables** (highest priority)
2. **`.env` file** in current directory
3. **Default values** in code (lowest priority)

### Example: Multiple Environments

You can use different `.env` files for different environments:

**Development** (`.env.development`):
```bash
MORTGAGE_DATA_DIR=/Users/username/data
```

**Production** (`.env.production`):
```bash
MORTGAGE_DATA_DIR=/data/mortgage_data
```

Load the appropriate file:

```python
from decouple import Config, RepositoryEnv

# Load production config
config = Config(RepositoryEnv('.env.production'))
```

---

## Directory Structure

### Default Directory Layout

The `data/` directory is not included in the repository. Directories are created automatically when you use the configuration classes or run CLI commands.

```
{MORTGAGE_DATA_DIR}/
├── hmda/
│   ├── raw/
│   ├── bronze/
│   ├── silver/
│   └── gold/
├── fha/
│   ├── raw/
│   ├── bronze/
│   └── silver/
├── gnma/
├── fhfa/
├── fnma/
├── fhlmc/
├── umbs/
├── hud/
├── hud_mf/
├── fhlb/
└── matching/
    ├── fha_hmda/
    ├── fhfa_hmda/
    ├── hmda_sellers_and_purchasers/
    ├── output/
    └── cache/
```

### Creating Directories

Directories are created automatically when you use the configuration classes:

```python
from mortgage_data_manager.hmda.config import HMDAConfig

# Ensure HMDA directories exist
HMDAConfig.ensure_directories()
```

---

## Per-Subpackage Configuration

### HMDA Configuration

```python
from mortgage_data_manager.hmda.config import HMDAConfig

config = HMDAConfig()

# Access configuration
print(config.HMDA_DATA_DIR)      # /data/hmda
print(config.HMDA_RAW_DIR)       # /data/hmda/raw
print(config.HMDA_BRONZE_DIR)    # /data/hmda/bronze
print(config.HMDA_SILVER_DIR)    # /data/hmda/silver

# Get medallion directory
silver = config.get_medallion_dir('hmda', 'silver')
```

### FHA Configuration

```python
from mortgage_data_manager.fha.config import FHAConfig

config = FHAConfig()

# Access configuration
print(config.FHA_DATA_DIR)       # /data/fha
print(config.FHA_RAW_DIR)        # /data/fha/raw
print(config.FHA_BRONZE_DIR)     # /data/fha/bronze
print(config.FHA_SILVER_DIR)     # /data/fha/silver
```

### MBS Configuration

```python
from mortgage_data_manager.gnma.config import MBSConfig

config = MBSConfig()

# Get agency-specific directories
gnma_dir = config.get_agency_data_dir('gnma')   # /data/mbs/gnma
fhfa_dir = config.get_agency_data_dir('fhfa')   # /data/mbs/fhfa
```

### FHLB Configuration

FHLB has its own config class:

```python
from mortgage_data_manager.fhlb.config import FHLBConfig

# Access FHLB-specific directories
fhlb_dir = FHLBConfig.FHLB_DATA_DIR   # /data/fhlb
```

### Matching Configuration

```python
from mortgage_data_manager.matching.config import MatchingConfig

config = MatchingConfig()

# Access configuration
print(config.MATCHING_DATA_DIR)      # /data/matching
print(config.MATCHING_OUTPUT_DIR)    # /data/matching/output
print(config.MATCHING_CACHE_DIR)     # /data/matching/cache

# Get workflow-specific directory
fha_hmda_dir = config.get_matching_type_dir('fha_hmda')  # /data/matching/fha_hmda
```

---

## Common Configuration Patterns

### Pattern 1: Centralized Data Directory

Store all data in one location:

```bash
# .env
MORTGAGE_DATA_DIR=/data/mortgage_data
```

All subpackages use subdirectories of `/data/mortgage_data/`:
- `/data/mortgage_data/hmda/`
- `/data/mortgage_data/fha/`
- `/data/mortgage_data/mbs/`
- etc.

### Pattern 2: Distributed Data Directories

Store each subpackage in a separate location:

```bash
# .env
HMDA_DATA_DIR=/data/hmda
FHA_DATA_DIR=/mnt/fha_data
MBS_DATA_DIR=/ssd/mbs_data
```

**Use case:** Different storage devices for different data types (SSD for frequently accessed, HDD for archives).

### Pattern 3: Development vs. Production

Use different directories for development and production:

**Development:**
```bash
# .env.development
MORTGAGE_DATA_DIR=/Users/username/dev_data
```

**Production:**
```bash
# .env.production
MORTGAGE_DATA_DIR=/data/mortgage_data
```

### Pattern 4: External Drive with Symlink

Store data on an external drive and symlink into the project:

```bash
# Copy data to external drive
rsync -av data/ /Volumes/ExternalSSD/mortgage_data/

# Remove local data and create symlink
rm -rf data/
ln -s /Volumes/ExternalSSD/mortgage_data data
```

The symlink is transparent — all reads and writes work as if the data were local. If the drive is not mounted, file operations will raise `FileNotFoundError`.

### Pattern 5: Network Storage

Store data on network/cloud storage:

```bash
# .env
MORTGAGE_DATA_DIR=/mnt/network_share/mortgage_data
# or
MORTGAGE_DATA_DIR=/Volumes/CloudStorage/mortgage_data
```

---

## Advanced Configuration

### Custom Path Resolution

Override specific paths programmatically:

```python
from mortgage_data_manager.core.config import MortgageDataConfig
from pathlib import Path

class CustomConfig(MortgageDataConfig):
    # Override with custom path
    DATA_DIR = Path("/custom/data/path")

config = CustomConfig()
```

### Dynamic Configuration

Load configuration based on runtime conditions:

```python
import os
from mortgage_data_manager.hmda.config import HMDAConfig

# Choose configuration based on environment
env = os.getenv("ENVIRONMENT", "development")

if env == "production":
    os.environ["HMDA_DATA_DIR"] = "/data/hmda"
else:
    os.environ["HMDA_DATA_DIR"] = "/Users/dev/hmda_data"

config = HMDAConfig()
```

### Validation

Validate paths before use:

```python
from mortgage_data_manager.core.config import MortgageDataConfig

config = MortgageDataConfig()

# Validate paths are accessible
try:
    config.validate_paths()
    print("✓ All paths are valid")
except PermissionError as e:
    print(f"✗ Permission error: {e}")
```

---

## Troubleshooting

### Problem: Configuration Not Loading

**Symptom:** Environment variables not being read

**Solutions:**

1. Check `.env` file location (must be in current directory)
   ```bash
   ls -la .env
   ```

2. Verify environment variable syntax
   ```bash
   # Correct
   HMDA_DATA_DIR=/data/hmda

   # Incorrect
   HMDA_DATA_DIR = /data/hmda  # No spaces around =
   ```

3. Check for typos in variable names
   ```python
   from mortgage_data_manager.hmda.config import HMDAConfig
   config = HMDAConfig()
   print(config.HMDA_DATA_DIR)  # Should print your custom path
   ```

### Problem: Permission Denied

**Symptom:** Cannot create directories or write files

**Solutions:**

1. Check directory permissions
   ```bash
   ls -ld /data/mortgage_data
   ```

2. Ensure write permissions
   ```bash
   chmod 755 /data/mortgage_data
   ```

3. Use a directory you own
   ```bash
   # .env
   MORTGAGE_DATA_DIR=/Users/username/mortgage_data
   ```

### Problem: Disk Space

**Symptom:** Running out of disk space

**Solutions:**

1. Check disk usage
   ```bash
   du -sh /data/mortgage_data/*
   ```

2. Use compression
   ```python
   # Already enabled by default in silver layer
   write_hive_partitioned(df, output_dir, compression="snappy")
   ```

3. Delete raw layer after processing
   ```bash
   rm -rf /data/hmda/raw/*
   ```

4. Use different drives for different subpackages
   ```bash
   # .env
   HMDA_DATA_DIR=/ssd/hmda      # Fast SSD for frequently accessed
   MBS_DATA_DIR=/hdd/mbs        # Large HDD for archives
   ```

### Problem: Path Not Found

**Symptom:** `FileNotFoundError` when accessing data

**Solutions:**

1. Ensure directories are created
   ```python
   from mortgage_data_manager.core.config import MortgageDataConfig
   MortgageDataConfig.ensure_directories('hmda')
   ```

2. Check configuration
   ```python
   from mortgage_data_manager.hmda.config import HMDAConfig
   config = HMDAConfig()
   print(f"HMDA data dir: {config.HMDA_DATA_DIR}")
   print(f"Exists: {config.HMDA_DATA_DIR.exists()}")
   ```

3. Create directories manually
   ```bash
   mkdir -p /data/mortgage_data/hmda/{raw,bronze,silver}
   ```

---

## Configuration Examples

### Example 1: Single Machine

```bash
# .env
MORTGAGE_DATA_DIR=/data/mortgage_data
```

**Directory structure:**
```
/data/mortgage_data/
├── hmda/
├── fha/
├── gnma/
└── matching/
```

### Example 2: Multiple Drives

```bash
# .env
# Fast SSD for frequently accessed data
HMDA_DATA_DIR=/ssd/hmda
FHA_DATA_DIR=/ssd/fha

# Large HDD for less frequently accessed data
MATCHING_DATA_DIR=/hdd/matching
```

### Example 3: Network Storage

```bash
# .env
# All data on network share
MORTGAGE_DATA_DIR=/mnt/nfs/mortgage_data

# Or specific subpackages
HMDA_DATA_DIR=/mnt/nfs/hmda
FHA_DATA_DIR=/mnt/nfs/fha
```

### Example 4: Docker Container

```bash
# .env
# Mount volumes to /data in container
MORTGAGE_DATA_DIR=/data
HMDA_DATA_DIR=/data/hmda
FHA_DATA_DIR=/data/fha
```

**docker-compose.yml:**
```yaml
services:
  mortgage-data:
    image: mortgage-data-manager
    volumes:
      - ./data:/data
    environment:
      - MORTGAGE_DATA_DIR=/data
```

---

## Best Practices

1. **Use .env files**: Keep configuration separate from code
2. **Version control**: Add `.env` to `.gitignore`, commit `.env.example`
3. **Document paths**: Comment your `.env` file
4. **Validate early**: Check paths exist before processing
5. **Use defaults**: Only override when necessary
6. **Consistent naming**: Follow `{SUBPACKAGE}_DATA_DIR` pattern

## See Also

- [Architecture Documentation](architecture.md) - System design
- [Core API](api/core.md) - Configuration classes
