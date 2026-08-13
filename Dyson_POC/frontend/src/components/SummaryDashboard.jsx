
import {
  Box,
  Card,
  CardContent,
  Grid,
  Typography,
  LinearProgress,
  Chip,
} from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RateReviewIcon from "@mui/icons-material/RateReview";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";

import { statusToken } from "../theme";

const BRAND_VIOLET = "#440099";
const BRAND_VIOLET_TINT = "#F3EEFA";
import SummarizeIcon from "@mui/icons-material/Summarize";
import EmojiEventsIcon from "@mui/icons-material/EmojiEvents";

function SummaryDashboard({ findings, coverage }) {
  if (!findings || findings.length === 0) return null;

  const total = findings.length;
  const compliant = findings.filter((f) => f.status === "COMPLIANT").length;
  const nonCompliant = findings.filter(
    (f) => f.status === "NON-COMPLIANT"
  ).length;
  const review = findings.filter((f) => f.status === "NEEDS_REVIEW").length;
  const notEvaluated = findings.filter(
    (f) => f.status === "NOT_EVALUATED"
  ).length;

  // Compliance is measured against what was actually tested. Counting rules
  // that never ran as a pass would inflate the score; counting them as a
  // failure would punish the design for a gap in the tool.
  const decided = compliant + nonCompliant;
  const compliancePct = decided > 0 ? Math.round((compliant / decided) * 100) : 0;
  const overallPass = nonCompliant === 0;
  const pctOfDecided = (n) =>
    decided > 0 ? `${Math.round((n / decided) * 100)}%` : "-";

  const verdictToken = statusToken(overallPass ? "COMPLIANT" : "NON-COMPLIANT");

  // The lead card is violet: it counts work done rather than reporting a
  // status, so it carries the brand colour instead of a status colour.
  const cards = [
    {
      title: "Checks Evaluated",
      value: decided,
      pct: `of ${total} rows`,
      icon: <SummarizeIcon sx={{ fontSize: 32 }} />,
      token: {
        main: BRAND_VIOLET,
        tint: BRAND_VIOLET_TINT,
        border: "rgba(68, 0, 153, 0.35)",
      },
    },
    {
      title: "Compliant",
      value: compliant,
      pct: pctOfDecided(compliant),
      icon: <CheckCircleIcon sx={{ fontSize: 32 }} />,
      token: statusToken("COMPLIANT"),
    },
    {
      title: "Non-Compliant",
      value: nonCompliant,
      pct: pctOfDecided(nonCompliant),
      icon: <CancelIcon sx={{ fontSize: 32 }} />,
      token: statusToken("NON-COMPLIANT"),
    },
    {
      title: "Needs Review",
      value: review,
      icon: <RateReviewIcon sx={{ fontSize: 32 }} />,
      token: statusToken("NEEDS_REVIEW"),
    },
    {
      title: "Not Evaluated",
      value: notEvaluated,
      pct: coverage ? `${coverage.rules_not_computable} rules` : undefined,
      icon: <HelpOutlineIcon sx={{ fontSize: 32 }} />,
      token: statusToken("NOT_EVALUATED"),
    },
    {
      title: "Overall Verdict",
      value: overallPass ? "PASS" : "FAIL",
      icon: <EmojiEventsIcon sx={{ fontSize: 32 }} />,
      token: verdictToken,
      isVerdict: true,
    },
  ];

  return (
    <Box sx={{ mb: 2 }}>
      <Grid container spacing={2}>
        {cards.map((card) => (
          <Grid item xs={6} sm={4} md key={card.title}>
            <Card
              sx={{
                height: "100%",
                position: "relative",
                overflow: "hidden",
                transition: "transform 0.15s, box-shadow 0.15s",
                "&:hover": {
                  transform: "translateY(-2px)",
                  boxShadow: "0 6px 18px rgba(4, 30, 66, 0.10)",
                },
                // A thin accent strip carries the card's colour, so the card
                // itself stays white and the numbers stay legible.
                "&::before": {
                  content: '""',
                  position: "absolute",
                  insetInline: 0,
                  top: 0,
                  height: 3,
                  backgroundColor: card.token.main,
                },
              }}
            >
              <CardContent sx={{ p: 2, "&:last-child": { pb: 2 } }}>
                <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1.25 }}>
                  <Box
                    sx={{
                      p: 1,
                      borderRadius: 2,
                      backgroundColor: card.token.tint,
                      color: card.token.main,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {card.icon}
                  </Box>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      noWrap
                      sx={{ fontWeight: 500 }}
                    >
                      {card.title}
                    </Typography>
                    {card.isVerdict ? (
                      <Box sx={{ mt: 0.5 }}>
                        <Chip
                          label={card.value}
                          size="small"
                          sx={{
                            fontWeight: 700,
                            fontSize: "0.8rem",
                            letterSpacing: 0.5,
                            backgroundColor: card.token.main,
                            color: card.token.contrastText,
                          }}
                        />
                      </Box>
                    ) : (
                      <Typography
                        variant="h5"
                        sx={{ fontWeight: 700, color: "text.primary" }}
                      >
                        {card.value}
                        {card.pct && (
                          <Typography
                            component="span"
                            variant="body2"
                            color="text.secondary"
                            sx={{ ml: 0.75, fontWeight: 500 }}
                          >
                            {card.pct}
                          </Typography>
                        )}
                      </Typography>
                    )}
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Box sx={{ mt: 2 }}>
        <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
          <Typography variant="body2" color="text.secondary">
            Compliance rate{" "}
            <Typography component="span" variant="caption" color="text.secondary">
              (of {decided} checks that produced a verdict)
            </Typography>
          </Typography>
          <Typography variant="body2" fontWeight={700}>
            {compliancePct}%
          </Typography>
        </Box>
        <LinearProgress
          variant="determinate"
          value={compliancePct}
          sx={{
            height: 8,
            // The filled portion *is* the compliant checks, so it takes the
            // compliant colour regardless of the overall verdict. Tying it to
            // the verdict would paint the passing share red.
            "& .MuiLinearProgress-bar": {
              backgroundColor: statusToken("COMPLIANT").main,
            },
          }}
        />
      </Box>
    </Box>
  );
}

export default SummaryDashboard;
