#!/usr/bin/env python3
import os
import json
import subprocess
import sys
import requests
from pathlib import Path

# --- CONFIGURATION ---
OLLAMA_URL = "http://127.0.0.1:11434/v1"
OLLAMA_MODEL = "qwen2.5-coder:7b"
TOKEN_BUDGET = 2000  # Smaller chunks to avoid OOM and long timeouts
OUT_DIR = Path("graphify-out")
GRAPH_FILE = OUT_DIR / "graph.json"

def run_cmd(cmd):
    print(f"Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
    return result.stdout

def main():
    print("🚀 Starting DCA/Grid Trading Bot Knowledge Graph Rebuild...")
    OUT_DIR.mkdir(exist_ok=True)

    # 1. Detect Files
    print("📂 Scanning project structure...")
    try:
        from graphify.detect import detect as graphify_detect
        detect_res = graphify_detect(Path("."))
        
        # Save for debugging/transparency
        with open(OUT_DIR / ".graphify_detect.json", "w") as f:
            json.dump(detect_res, f, indent=2)
        
        detect = detect_res
    except ImportError:
        print("Error: graphify package not found or incompatible.")
        sys.exit(1)
    except Exception as e:
        print(f"Error during file detection: {e}")
        sys.exit(1)
    
    all_files = []
    for category in detect['files']:
        all_files.extend(detect['files'][category])
    
    print(f"Found {len(all_files)} files to analyze.")

    # 2. Run Extraction (AST + Semantic) via CLI for robustness and caching
    print(f"🧠 Running Semantic Analysis via Ollama ({OLLAMA_MODEL})...")
    print("This will use the built-in cache to skip already analyzed files.")
    
    env = os.environ.copy()
    env["OLLAMA_BASE_URL"] = OLLAMA_URL
    env["OLLAMA_MODEL"] = OLLAMA_MODEL
    env["OLLAMA_API_KEY"] = "ollama"
    env["GRAPHIFY_CONCURRENCY"] = "1"  # Force sequential to save RAM
    env["GRAPH_TOKEN_BUDGET"] = str(TOKEN_BUDGET)
    
    # Using 'extract' command with --no-cluster so we can do clustering/tree manually later
    cmd = f"{sys.executable} -m graphify extract . --backend ollama --model {OLLAMA_MODEL} --no-cluster"
    subprocess.run(cmd, shell=True, env=env)
    
    # Unload model from memory to free up resources
    print(f"🧹 Unloading {OLLAMA_MODEL} from Ollama memory...")
    try:
        base_url = OLLAMA_URL.replace("/v1", "")
        requests.post(f"{base_url}/api/generate", json={"model": OLLAMA_MODEL, "keep_alive": 0}, timeout=5)
    except Exception as e:
        print(f"⚠️ Could not unload model: {e}")

    # 3. Clustering and Visualization
    print("📊 Clustering and generating HTML report...")
    run_cmd(f"{sys.executable} -m graphify cluster-only .")
    run_cmd(f"{sys.executable} -m graphify tree --graph {GRAPH_FILE}")

    print("\n✅ REBUILD COMPLETE!")
    print(f"📍 Graph Data: {GRAPH_FILE}")
    print(f"🌐 Visual Map: {OUT_DIR}/graph.html")
    print(f"🌳 Tree Map: {OUT_DIR}/GRAPH_TREE.html")
    print(f"📝 PRD Report: {OUT_DIR}/GRAPH_REPORT.md")

if __name__ == "__main__":
    main()
