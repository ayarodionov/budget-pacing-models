1. A number of clients are listening to one common source of bid requests.
2. A client has a finite number of tokens
3. Bit request hits clients randomly with equal probability
4. Every time a bid hits client it spends a token with some probability, i.e. client may rejects bid 
5. Client should not spend tokens too fast - should not to spend all at once 
6. There is a center with a finite budget and time during wich center has to spend the budget.
7. Overspending is critical and strickly forbidden. Undespanding should be avoid but not critical
8. Not every token realises in budget spending. Budget spend is proportional to number of spent tokens but this proprtion is not known in advance. 
9. Center does not know the number of clients
10. Center can send to cliens new token spend probability when needed.
11.  Center knows the current budget 
12.  

Is this information is enough for you to create an optimal startegy - spend as much budget as it is possible but not ovespend



A. Start with assumption that delay is fixed. Let it be a new parameter in the model
B. 1 token = 1 budget spend
C. Yes, immediatly
D. Let start with steady traffic
E. Let start with token-to-budget ratio is stable
F. Every bid request generate token, so there is no limit of tokenss in client. I introduce tokens to separate bid request from spending. In other words a bid request generates token with probability p; the probability is the same for each client and recived from the center.
G. See the answer to F.
H. p broadcast to everyone,
I. Very small overspend acceptible.  Let it be a parameter
J, 12 is empty - created by mistake 