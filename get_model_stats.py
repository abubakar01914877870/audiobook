import subprocess
import re
import sys
import concurrent.futures

def get_available_models():
    """Return the predefined prioritized list of Gemini models."""
    prioritized_models = [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ]
    print(f"Prioritized models: {', '.join(prioritized_models)}")
    return prioritized_models

def check_model_state(model):
    """Run gemini /stats and return the percentage of usage if found. Return 0 if failed."""
    print(f"Checking state for {model}...")
    try:
        # We pipe an empty string into it so it doesn't hang in interactive mode if /stats fails
        result = subprocess.run(
            ["gemini", "-m", model, "-p", "/stats", "-y"],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 130:
            raise KeyboardInterrupt
            
        output = result.stdout + "\n" + result.stderr        
        # Look for percentage like "20%" or "9%"
        match = re.search(r'(\d+)%', output)
        if match:
            percent = int(match.group(1))
            print(f"[{model}] State: {percent}%")
            return percent
        else:
            # Check if it hit a quota error explicitly
            if "exhausted your capacity" in output or "QuotaError" in output or "429" in output:
                print(f"[{model}] Quota exhausted.")
                return 0
            
            print(f"[{model}] Could not parse percentage from output. Assuming 100% to attempt.")
            # If we don't know the state, we can try to use it anyway
            return 100
    except subprocess.TimeoutExpired:
        print(f"[{model}] Timeout checking state. Assuming 100% to attempt.")
        return 100
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
    except Exception as e:
        print(f"[{model}] Error checking state: {e}")
        return 0

def main():
    models = get_available_models()
    
    # ---------------------------------------------------------
    # Check Model Usage Stats Before Starting
    # ---------------------------------------------------------
    print("\nGetting model usage status before starting...")
    model_states = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_model = {executor.submit(check_model_state, m): m for m in models}
        for future in concurrent.futures.as_completed(future_to_model):
            m = future_to_model[future]
            try:
                state = future.result()
                model_states[m] = state
            except Exception:
                model_states[m] = 0

    print("\n--- Model Usage Status ---")
    for m in models:
        state = model_states.get(m, 0)
        print(f"{m:<25} State: {state}%")
    print("--------------------------\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
