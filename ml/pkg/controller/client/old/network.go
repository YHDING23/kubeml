package old

import (
	"bytes"
	"encoding/json"
	"github.com/nwangfw/kubeml/ml/pkg/api"
	"github.com/pkg/errors"
	"io/ioutil"
)

// Train sends a train job to the controller
func (c *Client) Train(req *api.TrainRequest) (string, error) {
	url := c.controllerUrl + "/train"

	body, err := json.Marshal(req)
	if err != nil {
		return "", errors.Wrap(err, "could not send train job to the controller")
	}

	// send the request and get the task id
	// TODO this task id could be generated by the client
	resp, err := c.httpClient.Post(url, "application/json", bytes.NewBuffer(body))
	if err != nil {
		return "", errors.Wrap(err, "could not process train job")
	}

	defer resp.Body.Close()

	id, err := ioutil.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	return string(id), nil
}

// Infer submits an inference task to the scheduler and returns the response
// untouched as a byte array. This response will be a json object with the
// predictions from the inference task
func (c *Client) Infer(req *api.InferRequest) ([]byte, error) {
	url := c.controllerUrl + "/infer"

	// Create the request body
	body, err := json.Marshal(req)
	if err != nil {
		return nil, errors.Wrap(err, "could not send train request to scheduler")
	}

	// Send the request and return the id
	resp, err := c.httpClient.Post(url, "application/json", bytes.NewBuffer(body))
	if err != nil {
		return nil, errors.Wrap(err, "could not process inference job")
	}
	defer resp.Body.Close()

	body, err = ioutil.ReadAll(resp.Body)
	if err != nil {
		return nil, errors.Wrap(err, "could not read response body")
	}

	return body, nil
}
