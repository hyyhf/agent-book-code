---
name: Git Workflow
description: Common git commands and branching strategies
tags: [git, version-control]
---
# Git Workflow

## Basic Commands
```bash
# Clone a repository
git clone <repository-url>

# Check status
git status

# Add files
git add <file>
git add .  # add all changes

# Commit changes
git commit -m "Commit message"

# Push to remote
git push origin <branch>
```

## Branching Strategies

### Feature Branch Workflow
1. Create feature branch from main:
   ```bash
   git checkout -b feature/new-feature
   ```
2. Develop and commit changes
3. Push to remote:
   ```bash
   git push -u origin feature/new-feature
   ```
4. Create pull request
5. Merge after review

### Git Flow
- `main` - production releases
- `develop` - integration branch
- `feature/*` - new features
- `release/*` - release preparation
- `hotfix/*` - production fixes

## Common Workflows

### Starting a New Feature
```bash
git checkout main
git pull origin main
git checkout -b feature/feature-name
# make changes
git add .
git commit -m "Add feature"
git push -u origin feature/feature-name
```

### Updating Feature Branch
```bash
git checkout feature/feature-name
git fetch origin
git merge origin/main
# resolve conflicts if any
git push origin feature/feature-name
```

### Stashing Changes
```bash
# Save uncommitted changes
git stash

# List stashes
git stash list

# Apply stash
git stash pop

# Apply specific stash
git stash apply stash@{0}
```

## Useful Aliases
```bash
# Add to ~/.gitconfig
[alias]
    co = checkout
    br = branch
    ci = commit
    st = status
    lg = log --oneline --graph --all
    unstage = reset HEAD --
    last = log -1 HEAD
```