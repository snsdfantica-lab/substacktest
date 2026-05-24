name: Daily Semis Substack Digest

on:
  workflow_dispatch:
    inputs:
      since_days:
        description: 'How many days back to look (default 1)'
        required: false
        default: '1'
      authors_filter:
        description: 'Optional: comma-separated author names to run only (blank = all)'
        required: false
        default: ''
      skip_synthesis:
        description: 'Skip the Opus cross-article synthesis (true/false)'
        required: false
        default: 'false'
      dry_run:
        description: 'Dry run: do not write to Notion or update seen.json (true/false)'
        required: false
        default: 'false'

permissions:
  contents: write  # needed to commit seen.json + cache back

concurrency:
  group: digest
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Build CLI flags
        id: flags
        run: |
          FLAGS="--since-days ${{ inputs.since_days }}"
          if [ -n "${{ inputs.authors_filter }}" ]; then
            FLAGS="$FLAGS --authors \"${{ inputs.authors_filter }}\""
          fi
          if [ "${{ inputs.skip_synthesis }}" = "true" ]; then
            FLAGS="$FLAGS --skip-synthesis"
          fi
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            FLAGS="$FLAGS --dry-run"
          fi
          echo "flags=$FLAGS" >> "$GITHUB_OUTPUT"

      - name: Run digest
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_PARENT_DB_ID: ${{ secrets.NOTION_PARENT_DB_ID }}
          SUBSTACK_COOKIE: ${{ secrets.SUBSTACK_COOKIE }}
        run: |
          eval python -m src.main ${{ steps.flags.outputs.flags }}

      - name: Commit state changes
        if: ${{ inputs.dry_run != 'true' }}
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add state/
          if git diff --cached --quiet; then
            echo "No state changes to commit."
          else
            git commit -m "chore(state): update after digest run $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            git push
          fi
