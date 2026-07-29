UPDATE app_users
SET onboarding_required = FALSE
WHERE onboarding_required = TRUE;
