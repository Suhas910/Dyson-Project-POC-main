import { useState, useCallback, useEffect, useMemo } from "react";
import {
  Box,
  Grid,
  Alert,
  Button,
  Typography,
  Paper,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import axios from "axios";

import Header from "./components/Header";
import ExecutiveSummary from "./components/ExecutiveSummary";
import ProcessClassification from "./components/ProcessClassification";
import VersionComparison from "./components/VersionComparison";
import PipelineStepper from "./components/PipelineStepper";
import SummaryDashboard from "./components/SummaryDashboard";
import ValidationIssuesPanel from "./components/ValidationIssuesPanel";
import Viewer from "./components/Viewer";
import PartViewer from "./components/PartViewer";
import FindingsTable from "./components/FindingsTable";

function App() {
  const [findings, setFindings] = useState([]);
  const [rulesApplied, setRulesApplied] = useState([]);
  const [validationIssues, setValidationIssues] = useState([]);
  const [coverage, setCoverage] = useState(null);
  const [summary, setSummary] = useState(null);
  const [llmStatus, setLlmStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [analysisComplete, setAnalysisComplete] = useState(false);
  const [versionId, setVersionId] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [classification, setClassification] = useState(null);
  const [analysisMode, setAnalysisMode] = useState("fresh");
  const [versions, setVersions] = useState([]);
  const [selectedPreviousVersion, setSelectedPreviousVersion] = useState("");
  // Which face the user clicked in the 3D view, if any. Null means no filter.
  const [selectedFace, setSelectedFace] = useState(null);

  const handleFileUpload = useCallback(async (formData) => {
    setLoading(true);
    setError(null);
    setFindings([]);
    setRulesApplied([]);
    setValidationIssues([]);
    setCoverage(null);
    setSummary(null);
    setLlmStatus(null);
    setClassification(null);
    setComparison(null);
    setSelectedFace(null);
    setAnalysisComplete(false);
    setUploadedFile(formData.get("file")); // Store the File object for the Header chip

    try {
      const response = await axios.post("/api/analyze", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      const data = response.data;
      setFindings(data.findings || []);
      setRulesApplied(data.rules_applied || []);
      setValidationIssues(data.validation_issues || []);
      setCoverage(data.coverage || null);
      setSummary(data.summary || null);
      setLlmStatus(data.llm || null);
      setClassification(data.classification || null);
      setVersionId(data.version_id);

  

    } catch (err) {
      console.error("Error uploading file:", err);
      let detail = "Unknown error";

      if (err.code === "ERR_NETWORK") {
        detail =
          "Cannot connect to the analysis server. Please ensure the backend is running at http://127.0.0.1:8001";
      } else if (err?.response?.status === 400) {
        detail =
          err?.response?.data?.detail ||
          "Invalid file type. Please upload a .step or .stp file.";
      } else if (err?.response?.status === 500) {
        detail =
          err?.response?.data?.detail ||
          "The server encountered an error during analysis.";
      } else {
        detail = err?.response?.data?.detail || err?.message || "Unknown error";
      }

      setError(detail);
    } finally {
      setLoading(false);
      setAnalysisComplete(true);
    }
  }, []);

  useEffect(() => {
  if (analysisMode !== "compare") {
    return;
  }

  const loadVersions = async () => {
    try {
      const response = await axios.get("/api/versions");
      setVersions(response.data.versions || []);
    } catch (err) {
      console.error("Unable to load previous analyses:", err);
    }
  };

  loadVersions();
}, [analysisMode]);

const handleCompare = useCallback(async () => {
  if (!selectedPreviousVersion || !versionId) {
    return;
  }

  try {
    const response = await axios.get(
      `/api/compare/${selectedPreviousVersion}/${versionId}`
    );

    setComparison(response.data);

    console.log("Version comparison:", response.data);
  } catch (err) {
    console.error("Version comparison failed:", err);

    setError(
      err?.response?.data?.detail ||
        "Unable to compare the selected versions."
    );
  }
}, [selectedPreviousVersion, versionId]);

  const handleClearFile = useCallback(() => {
    setUploadedFile(null);
    setFindings([]);
    setRulesApplied([]);
    setValidationIssues([]);
    setError(null);
    setAnalysisComplete(false);
  }, []);

  const handleRetry = useCallback(() => {
    // A simple retry isn't possible without knowing the process_family,
    // which is managed inside the Header. We'll just clear the error
    // and prompt the user to upload again.
    setError(null);
    handleClearFile();
  }, [handleClearFile]);

  const hasResults = analysisComplete && !error;

  // Clicking a face in the 3D view narrows the table to that face's findings,
  // which is the whole point of the view: "face 214" in a table means nothing
  // until you can see which face it is, and seeing it means nothing until you
  // can read what is wrong with it.
  const visibleFindings = useMemo(
    () =>
      selectedFace == null
        ? findings
        : findings.filter((f) => f.location === `face ${selectedFace}`),
    [findings, selectedFace]
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <Header
        onFileUpload={handleFileUpload}
        loading={loading}
        uploadedFile={uploadedFile}
        onClearFile={handleClearFile}
      />
      <Paper sx={{ p: 2, mb: 2 }}>
  <Typography variant="h6" fontWeight={700} sx={{ mb: 1 }}>
    Analysis Mode
  </Typography>

  <Box sx={{ display: "flex", gap: 2 }}>
    <Button
      variant={analysisMode === "fresh" ? "contained" : "outlined"}
      onClick={() => {
        setAnalysisMode("fresh");
        setComparison(null);
        setSelectedPreviousVersion("");
      }}
    >
      Fresh Analysis
    </Button>

    <Button
      variant={analysisMode === "compare" ? "contained" : "outlined"}
      onClick={() => setAnalysisMode("compare")}
    >
      Compare with Previous
    </Button>
  </Box>

  {analysisMode === "compare" && (
    <Box sx={{ mt: 2 }}>
      <FormControl fullWidth>
        <InputLabel>Previous Analysis</InputLabel>

        <Select
          value={selectedPreviousVersion}
          label="Previous Analysis"
          onChange={(event) =>
            setSelectedPreviousVersion(event.target.value)
          }
        >
          {versions.map((version) => (
            <MenuItem
              key={version.id}
              value={String(version.id)}
            >
              Version {version.id} — {version.file_name} —{" "}
              {version.process_family}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    </Box>
  )}

  {analysisMode === "compare" && versionId && (
    <Button
      sx={{ mt: 2 }}
      variant="contained"
      onClick={handleCompare}
      disabled={
        !selectedPreviousVersion ||
        String(versionId) === String(selectedPreviousVersion)
      }
    >
      Compare Versions
    </Button>
  )}
</Paper>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: { xs: 1.5, sm: 2 },
          backgroundColor: "background.default",
          overflow: "auto",
        }}
      >
        {(loading || analysisComplete) && (
          <PipelineStepper loading={loading} error={error} />
        )}

        {error && (
          <Alert
            severity="error"
            sx={{ mb: 2 }}
            action={
              <Button
                color="inherit"
                size="small"
                startIcon={<RefreshIcon />}
                onClick={handleRetry}
              >
                Retry
              </Button>
            }
          >
            <Typography variant="subtitle2" fontWeight={600}>
              Analysis Error
            </Typography>
            {error}
          </Alert>
        )}

        {!loading && !error && !hasResults && (
          <Paper
            sx={{
              p: 6,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 2,
              textAlign: "center",
            }}
          >
            <Box
              sx={{
                width: 80,
                height: 80,
                borderRadius: "50%",
                backgroundColor: "brand.violetTint",
                color: "primary.main",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <TravelExploreIcon sx={{ fontSize: 40 }} />
            </Box>
            <Typography variant="h5" fontWeight={700}>
              DFM Analysis Dashboard
            </Typography>
            <Typography
              variant="body1"
              color="text.secondary"
              sx={{ maxWidth: 500 }}
            >
              Upload a STEP file to run a comprehensive Design for
              Manufacturability analysis. The system will check wall thickness,
              detect undercuts, and evaluate cosmetic features across all faces
              of your 3D part model.
            </Typography>
            <Box
              sx={{
                display: "flex",
                gap: 1,
                flexWrap: "wrap",
                justifyContent: "center",
              }}
            >
              {[
                "Injection Moulding",
                "Sheet Metal",
                "Machining",
                "And more...",
              ].map((rule) => (
                <Paper key={rule} variant="outlined" sx={{ px: 1.5, py: 0.5 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    fontWeight={600}
                  >
                    {rule}
                  </Typography>
                </Paper>
              ))}
            </Box>
          </Paper>
        )}

        {hasResults && !error && (
          <>
            <ProcessClassification classification={classification} />
            <ExecutiveSummary summary={summary} llm={llmStatus} />
            <SummaryDashboard findings={findings} coverage={coverage} />
            <VersionComparison comparison={comparison} />
            <ValidationIssuesPanel issues={validationIssues} />
            <PartViewer
              versionId={versionId}
              findings={findings}
              selectedFace={selectedFace}
              onSelectFace={setSelectedFace}
            />
            <Grid container spacing={2}>
              <Grid item xs={12} md={3}>
                <Paper sx={{ height: "100%", overflow: "auto" }}>
                  <Viewer findings={findings} rules={rulesApplied} />
                </Paper>
              </Grid>
              <Grid item xs={12} md={9}>
                <FindingsTable
                  findings={visibleFindings}
                  versionId={versionId}
                  rules={rulesApplied}
                />
              </Grid>
            </Grid>
          </>
        )}
      </Box>
    </Box>
  );
}

export default App;
