import { useState, useEffect, useRef, useCallback } from "react";
import {
  Box,
  Paper,
  Stepper,
  Step,
  StepLabel,
  Typography,
  useTheme,
} from "@mui/material";
import SettingsSuggestIcon from "@mui/icons-material/SettingsSuggest";
import DataUsageIcon from "@mui/icons-material/DataUsage";
import PlayCircleIcon from "@mui/icons-material/PlayCircle";
import PsychologyIcon from "@mui/icons-material/Psychology";
import VerifiedIcon from "@mui/icons-material/Verified";

const PIPELINE_STEPS = [
  {
    label: "Ingest",
    description: "Loading STEP file geometry",
    icon: <SettingsSuggestIcon />,
  },
  {
    label: "Extract",
    description: "Computing face normals, draft angles, wall thickness",
    icon: <DataUsageIcon />,
  },
  {
    label: "Execute",
    description: "Evaluating DFM rules against part model",
    icon: <PlayCircleIcon />,
  },
  {
    label: "Interpret",
    description: "Generating AI commentary for REVIEW findings",
    icon: <PsychologyIcon />,
  },
  {
    label: "Validate",
    description: "Cross-checking findings for consistency",
    icon: <VerifiedIcon />,
  },
];

const STEP_DELAYS = [1200, 1800, 1500, 1000, 600];

function PipelineStepper({ loading, error }) {
  const [displayStep, setDisplayStep] = useState(-1);
  const [visible, setVisible] = useState(false);
  const theme = useTheme();
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  const clearTimers = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimers();
    };
  }, [clearTimers]);

  useEffect(() => {
    clearTimers();

    if (loading) {
      setVisible(true);
      setDisplayStep(0);

      const advance = (step) => {
        if (!mountedRef.current) return;
        if (step > PIPELINE_STEPS.length) return;

        setDisplayStep(step);

        if (step <= PIPELINE_STEPS.length - 1) {
          timerRef.current = setTimeout(
            () => advance(step + 1),
            STEP_DELAYS[step] || 1000
          );
        }
      };

      timerRef.current = setTimeout(
        () => advance(1),
        STEP_DELAYS[0]
      );
      return () => clearTimers();
    }

    if (displayStep >= 0 && !loading) {
      const finalStep = PIPELINE_STEPS.length;
      setDisplayStep(finalStep);
      timerRef.current = setTimeout(() => {
        if (mountedRef.current) setVisible(false);
      }, 800);
      return () => clearTimers();
    }
  }, [loading, clearTimers, displayStep]);

  if (!visible && displayStep < 0) return null;

  const clampedStep =
    displayStep > PIPELINE_STEPS.length
      ? PIPELINE_STEPS.length
      : displayStep;

  const getStepState = (index) => {
    if (error && index === clampedStep) return "error";
    if (index < clampedStep) return "completed";
    if (index === clampedStep && loading) return "active";
    if (clampedStep >= PIPELINE_STEPS.length) return "completed";
    return "pending";
  };

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1, color: "text.secondary" }}>
        Analysis Pipeline
      </Typography>
      <Stepper activeStep={clampedStep} alternativeLabel sx={{ py: 1 }}>
        {PIPELINE_STEPS.map((step, index) => {
          const state = getStepState(index);
          return (
            <Step
              key={step.label}
              completed={state === "completed"}
              error={state === "error"}
            >
              <StepLabel
                StepIconComponent={() => {
                  // Violet marks work already done, blue marks the step in
                  // flight, and muted violet marks what is still ahead -- so
                  // progress reads left to right without relying on colour
                  // alone to distinguish success from failure.
                  const marker = {
                    active: {
                      background: theme.palette.secondary.main,
                      color: "#FFFFFF",
                    },
                    completed: {
                      background: theme.palette.primary.main,
                      color: "#FFFFFF",
                    },
                    error: {
                      background: theme.palette.error.main,
                      color: "#FFFFFF",
                    },
                    pending: {
                      background: theme.palette.brand.violetTint,
                      color: theme.palette.brand.violetMuted,
                    },
                  }[state];

                  return (
                    <Box
                      sx={{
                        width: 32,
                        height: 32,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        backgroundColor: marker.background,
                        color: marker.color,
                        ...(state === "active" && {
                          animation: "pulse 1.5s infinite",
                          "@keyframes pulse": {
                            "0%": { boxShadow: "0 0 0 0 rgba(0, 87, 184, 0.45)" },
                            "70%": { boxShadow: "0 0 0 10px rgba(0, 87, 184, 0)" },
                            "100%": { boxShadow: "0 0 0 0 rgba(0, 87, 184, 0)" },
                          },
                        }),
                      }}
                    >
                      {step.icon}
                    </Box>
                  );
                }}
              >
                <Typography
                  variant="body2"
                  fontWeight={state === "active" ? 700 : 500}
                  color={
                    state === "active"
                      ? "secondary.main"
                      : state === "completed"
                      ? "text.primary"
                      : state === "error"
                      ? "error"
                      : "text.secondary"
                  }
                >
                  {step.label}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {state === "active"
                    ? step.description
                    : state === "completed"
                    ? "Done"
                    : state === "error"
                    ? "Failed"
                    : "Pending"}
                </Typography>
              </StepLabel>
            </Step>
          );
        })}
      </Stepper>
    </Paper>
  );
}

export default PipelineStepper;
