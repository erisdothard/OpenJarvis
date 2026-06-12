#!/bin/bash
# Setup Gmail Pub/Sub push notifications for OpenJarvis alerts.
# Run once. Requires: gcloud CLI authenticated with a GCP project.

set -euo pipefail

TOPIC="gmail-notifications"
SUBSCRIPTION="gmail-pull-sub"

# Get current project
PROJECT=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT" ]; then
    echo "ERROR: No GCP project set. Run: gcloud init"
    exit 1
fi
echo "Using GCP project: $PROJECT"

# Enable Pub/Sub API
echo "Enabling Pub/Sub API..."
gcloud services enable pubsub.googleapis.com --quiet

# Create topic (ignore if exists)
echo "Creating topic: $TOPIC"
gcloud pubsub topics create "$TOPIC" 2>/dev/null || echo "  (topic already exists)"

# Grant Gmail publish access
echo "Granting Gmail publish access..."
gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
    --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
    --role="roles/pubsub.publisher" \
    --quiet

# Create pull subscription (ignore if exists)
echo "Creating pull subscription: $SUBSCRIPTION"
gcloud pubsub subscriptions create "$SUBSCRIPTION" \
    --topic="$TOPIC" \
    --ack-deadline=60 \
    2>/dev/null || echo "  (subscription already exists)"

# Set up Application Default Credentials
echo ""
echo "Setting up Application Default Credentials..."
gcloud auth application-default login --quiet 2>/dev/null || \
    echo "  (already authenticated or run manually: gcloud auth application-default login)"

# Update config.toml
CONFIG="$HOME/.openjarvis/config.toml"
if grep -q "\[alerts.gmail_push\]" "$CONFIG" 2>/dev/null; then
    echo "  [alerts.gmail_push] section already exists in config.toml"
else
    echo "" >> "$CONFIG"
    cat >> "$CONFIG" << EOF

[alerts.gmail_push]
gcp_project = "$PROJECT"
topic = "$TOPIC"
subscription = "$SUBSCRIPTION"
important_senders = []
EOF
    echo "  Added [alerts.gmail_push] to $CONFIG"
fi

echo ""
echo "Done! Gmail push notifications configured."
echo "  Project:      $PROJECT"
echo "  Topic:        projects/$PROJECT/topics/$TOPIC"
echo "  Subscription: projects/$PROJECT/subscriptions/$SUBSCRIPTION"
echo ""
echo "Restart the Jarvis server to activate the Gmail listener."
