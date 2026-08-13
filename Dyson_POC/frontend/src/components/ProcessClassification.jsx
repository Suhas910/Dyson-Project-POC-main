import { useState } from "react";
import {
  Box,
  Paper,
  Typography,
  Chip,
  Alert,
  Collapse,
  Button,
  Divider,
  Tooltip,
} from "@mui/material";
import PrecisionManufacturingIcon from "@mui/icons-material/PrecisionManufacturing";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";

import { PANTONE } from "../theme";

// How a candidate is presented depends on why it is in the list, not only on
// how well it scored. A family that is always applicable did not "win" anything
// and should not look like it did.
const CONFIDENCE_STYLE = {
  likely: { color: PANTONE.violet, tint: "#F3EEFA", border: "rgba(68,0,153,0.35)" },
  possible: { color: "#8A6400", tint: "#FCF4DE", border: "rgba(234,170,0,0.5)" },
  unlikely: { color: "#565E6B", tint: "#F1F2F5", border: "rgba(107,114,128,0.3)" },
  "always applicable": {
    color: PANTONE.blue,
    tint: "#E8F0FA",
    border: "rgba(0,87,184,0.35)",
  },
  assembly: { color: PANTONE.blue, tint: "#E8F0FA", border: "rgba(0,87,184,0.35)" },
};

function styleFor(confidence) {
  return CONFIDENCE_STYLE[confidence] || CONFIDENCE_STYLE.unlikely;
}

function EvidenceList({ items, positive }) {
  if (!items?.length) return null;
  return (
    <Box component="ul" sx={{ m: 0, pl: 0, listStyle: "none", display: "grid", gap: 0.5 }}>
      {items.map((item) => (
        <Box
          component="li"
          key={item}
          sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}
        >
          {positive ? (
            <CheckIcon sx={{ fontSize: 16, mt: "2px", color: "#006B31" }} />
          ) : (
            <CloseIcon sx={{ fontSize: 16, mt: "2px", color: "#A6192E" }} />
          )}
          <Typography variant="body2" color="text.secondary">
            {item}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

function ProcessClassification({ classification }) {
  const [expanded, setExpanded] = useState(false);

  if (!classification) return null;

  const { candidates = [], signals = {}, notes = [], reading } = classification;
  const analysed = new Set(classification.families_to_analyse || []);
  const detected = candidates.filter((c) => c.basis === "detected");
  const included = candidates.filter((c) => analysed.has(c.process_family));

  return (
    <Paper sx={{ mb: 2, overflow: "hidden" }}>
      <Box
        sx={{
          px: 2,
          py: 1.5,
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <PrecisionManufacturingIcon sx={{ color: "primary.main" }} />
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          How this part was read
        </Typography>
        <Chip
          size="small"
          label={`${included.length} ${
            included.length === 1 ? "rule set" : "rule sets"
          } applied`}
          sx={{ fontWeight: 600 }}
        />
      </Box>

      <Box sx={{ p: 2, display: "grid", gap: 2 }}>
        {reading?.reading && (
          <Box>
            <Typography variant="body1" sx={{ lineHeight: 1.65 }}>
              {reading.reading}
            </Typography>
            {reading.caveat && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 1, fontStyle: "italic" }}
              >
                {reading.caveat}
              </Typography>
            )}
          </Box>
        )}

        {notes.map((note) => (
          <Alert key={note} severity="info" sx={{ py: 0.5 }}>
            {note}
          </Alert>
        ))}

        {/* The families themselves. Everything analysed is shown, whether it
            was detected or applies universally, because a reader needs to know
            which sections of the report exist and why. */}
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          {candidates.map((candidate) => {
            const style = styleFor(candidate.confidence);
            const isAnalysed = analysed.has(candidate.process_family);
            return (
              <Tooltip
                key={candidate.process_family}
                title={
                  isAnalysed
                    ? "Checked against this rule set"
                    : "Not checked — the evidence did not support it"
                }
              >
                <Chip
                  label={`${candidate.process_family} · ${candidate.confidence}`}
                  size="small"
                  variant={isAnalysed ? "filled" : "outlined"}
                  sx={{
                    fontWeight: 600,
                    backgroundColor: isAnalysed ? style.tint : "transparent",
                    color: isAnalysed ? style.color : "text.disabled",
                    border: `1px solid ${
                      isAnalysed ? style.border : "rgba(0,0,0,0.12)"
                    }`,
                    opacity: isAnalysed ? 1 : 0.65,
                  }}
                />
              </Tooltip>
            );
          })}
        </Box>

        <Box>
          <Button
            size="small"
            onClick={() => setExpanded((open) => !open)}
            endIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          >
            {expanded ? "Hide the evidence" : "Show the evidence"}
          </Button>

          <Collapse in={expanded}>
            <Box sx={{ pt: 2, display: "grid", gap: 2 }}>
              {/* The measurements first: every score below was computed from
                  these, so a reader who disagrees with a score can see exactly
                  what it was reasoning from. */}
              <Box>
                <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
                  What was measured
                </Typography>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "1fr",
                      sm: "repeat(2, 1fr)",
                      md: "repeat(3, 1fr)",
                    },
                    gap: 1,
                  }}
                >
                  {Object.entries(signals).map(([name, signal]) => (
                    <Box
                      key={name}
                      sx={{
                        px: 1.5,
                        py: 1,
                        borderRadius: 1,
                        backgroundColor: "background.default",
                        border: 1,
                        borderColor: "divider",
                      }}
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ textTransform: "capitalize" }}
                      >
                        {name.replace(/_/g, " ")}
                      </Typography>
                      <Typography variant="body2" fontWeight={600}>
                        {signal.display}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </Box>

              <Divider />

              {detected.map((candidate) => (
                <Box key={candidate.process_family}>
                  <Typography variant="subtitle2" fontWeight={700}>
                    {candidate.process_family}
                    <Typography
                      component="span"
                      variant="caption"
                      color="text.secondary"
                      sx={{ ml: 1 }}
                    >
                      scored {Math.round(candidate.score * 100)}%
                    </Typography>
                  </Typography>
                  <Box sx={{ mt: 0.5, display: "grid", gap: 0.5 }}>
                    <EvidenceList items={candidate.evidence_for} positive />
                    <EvidenceList items={candidate.evidence_against} />
                  </Box>
                </Box>
              ))}
            </Box>
          </Collapse>
        </Box>
      </Box>
    </Paper>
  );
}

export default ProcessClassification;
